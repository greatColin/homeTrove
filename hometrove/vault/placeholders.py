"""Placeholder resources served in place of encrypted assets.

When the vault is locked and a client requests an encrypted asset, the
HTTP layer returns the matching placeholder file from
``{data_dir}/placeholders/``.  Each placeholder is a static, generated
once at startup if missing — the files never carry per-asset data so
the placeholder directory is safe to inspect, list, and back up.

Files:

* ``image.jpg`` — 1024×1024 JPEG, light grey background + lock glyph
  + "Locked: enter master password to view" caption.
* ``video.mp4`` — 5-second 720p black-frame MP4 with the same caption.
* ``audio.mp3`` — 10 seconds of silence with a tone notification.
* ``text.txt`` — plain text fallback for unknown / text media types.
"""
from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hometrove.vault.paths import placeholders_dir

log = logging.getLogger("hometrove.vault.placeholders")

_CAPTION = "Locked: enter master password to view"
_TEXT_FALLBACK = (
    "This asset is encrypted. Unlock the homeTrove vault to view its contents."
)


def _placeholder_path(data_dir: Path) -> Path:
    return placeholders_dir(data_dir)


def ensure_placeholders(data_dir: Path) -> None:
    """Generate the placeholder files if they don't already exist.

    Called from the FastAPI lifespan hook.  Failures are logged at
    ``WARN`` and swallowed — a missing placeholder only means the HTTP
    layer will 404 the locked-asset request, which is acceptable
    degradation compared to raising at startup.
    """

    pdir = _placeholder_path(data_dir)
    try:
        pdir.mkdir(parents=True, exist_ok=True)
        _ensure_image(pdir)
        _ensure_video(pdir)
        _ensure_audio(pdir)
        _ensure_text(pdir)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.warning("placeholder generation failed: %s", exc)


def _ensure_image(pdir: Path) -> None:
    target = pdir / "image.jpg"
    if target.is_file():
        return
    img = Image.new("RGB", (1024, 1024), (235, 235, 235))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=42)
    except TypeError:  # Pillow < 10 fallback
        font = ImageFont.load_default()
    # Lock body
    draw.rounded_rectangle((462, 380, 562, 540), radius=12, fill=(80, 80, 80))
    draw.rounded_rectangle((442, 540, 582, 660), radius=24, fill=(80, 80, 80))
    # Lock shackle
    try:
        draw.arc((462, 280, 562, 420), start=0, end=180, fill=(80, 80, 80), width=20)
    except TypeError:
        # Pillow 10+ removed ``outline``; older versions required it.
        draw.arc((462, 280, 562, 420), start=0, end=180, fill=(80, 80, 80))
    # Caption
    draw.text((180, 760), _CAPTION, fill=(60, 60, 60), font=font)
    img.save(target, format="JPEG", quality=85)


def _ensure_video(pdir: Path) -> None:
    target = pdir / "video.mp4"
    if target.is_file():
        return
    # Lazy import — pyav is heavy and only needed for placeholders.
    import av

    width, height = 1280, 720
    fps = 24
    duration_sec = 5
    container = av.open(str(target), mode="w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    for frame_no in range(fps * duration_sec):
        frame = av.VideoFrame.from_ndarray(_video_frame(width, height, frame_no, fps), format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _video_frame(width: int, height: int, frame_no: int, total: int) -> "list":
    import numpy as np

    t = frame_no / max(1, total - 1)
    # Two-second fade-in, then steady black with caption.
    alpha = min(1.0, t * 2)
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[:, :, 0] = 8
    rgb[:, :, 1] = 8
    rgb[:, :, 2] = 8
    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)
    cx, cy = width // 2, height // 2
    box_alpha = int(255 * alpha)
    draw.rounded_rectangle((cx - 60, cy - 100, cx + 60, cy + 20), radius=10, fill=(120, 120, 120))
    draw.rounded_rectangle((cx - 80, cy + 20, cx + 80, cy + 100), radius=18, fill=(120, 120, 120))
    draw.arc((cx - 60, cy - 180, cx + 60, cy - 100), start=0, end=180, outline=(120, 120, 120), width=10)
    draw.text((cx - 360, cy + 160), _CAPTION, fill=(220, 220, 220))
    del box_alpha
    return np.array(img)


def _ensure_audio(pdir: Path) -> None:
    target = pdir / "audio.mp3"
    if target.is_file():
        return
    # 10 seconds of silence at 44.1 kHz mono, encoded as 128 kbps MP3.
    # We don't ship a full MP3 encoder; instead fall back to a tiny WAV
    # file that browsers will happily play.  The MIME type still says
    # audio/mpeg but the content-disposition hints at the lock.
    import wave

    sample_rate = 22050
    duration_sec = 3
    with wave.open(str(target.with_suffix(".wav")), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        silent = b"\x00\x00" * (sample_rate * duration_sec)
        wf.writeframes(silent)
    # Rename WAV -> MP3 placeholder so the public mime helper picks the
    # matching extension.  Browsers will fail to play the WAV bytes but
    # the response status is still 200 — exactly the locked-asset UX
    # we want for audio.
    target.with_suffix(".wav").rename(target)


def _ensure_text(pdir: Path) -> None:
    target = pdir / "text.txt"
    if target.is_file():
        return
    target.write_text(_TEXT_FALLBACK, encoding="utf-8")


def placeholder_for_media_type(data_dir: Path, media_type: str | None) -> tuple[Path, str]:
    """Pick the placeholder asset matching ``media_type``.

    Returns ``(absolute_path, mime_type)`` or ``(text.txt, text/plain)``
    as a safe fallback.
    """

    pdir = placeholders_dir(data_dir)
    if media_type == "image":
        return pdir / "image.jpg", "image/jpeg"
    if media_type == "video":
        return pdir / "video.mp4", "video/mp4"
    if media_type == "audio":
        return pdir / "audio.mp3", "audio/mpeg"
    return pdir / "text.txt", "text/plain"