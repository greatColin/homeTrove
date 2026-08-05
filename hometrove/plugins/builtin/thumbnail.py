"""``thumbnail`` plugin (M1-1).

Generates downscaled JPEG copies of an asset into ``{data_dir}/thumbs/{asset_id}/``:

* images are resized with Pillow (keeps EXIF orientation, no upscale);
* videos get a representative frame via ``ffmpeg`` when available; otherwise a
  deterministic placeholder PNG is written so the grid never shows a broken
  image for video rows.

The plugin is *not* a failure when it cannot produce a real thumbnail (e.g.
unsupported format, missing ffmpeg): it records a ``skipped`` result so the
frontend falls back to the original file / a labeled tile.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin

# Fixed size buckets. Keys are the URL query value for ``/api/assets/{id}/thumbnail``.
_SIZES = {
    "small": 320,
    "medium": 1280,
}

_DEFAULT_MAX_SIZE = _SIZES["small"]


class ThumbnailPlugin(BasePlugin):
    id: str = "thumbnail"
    name: str = "缩略图"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    depends_on: list[str] = []

    class ParamsModel(BaseModel):
        sizes: list[str] = list(_SIZES.keys())
        quality: int = 82
        video_frame_at_sec: float = 0.0

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.15, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: ThumbnailPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        if ctx.data_dir is None:
            return {"status": "skipped", "reason": "no data_dir in context"}

        raw = asset.path
        if "\0" in raw:
            _root, rel = raw.split("\0", 1)
            src = Path(asset.media_root) / rel
        elif Path(raw).is_absolute():
            src = Path(raw)
        else:
            src = Path(asset.media_root) / raw
        if not src.is_file():
            return {"status": "skipped", "reason": "source file missing"}

        out_dir = ctx.data_dir / "thumbs" / str(asset.id)
        out_dir.mkdir(parents=True, exist_ok=True)

        want = [s for s in params.sizes if s in _SIZES]
        sizes = want or [_DEFAULT_MAX_SIZE]
        produced: dict[str, str] = {}

        if asset.media_type == MediaType.VIDEO.value:
            ok, src = self._video_frame(src, out_dir, params.video_frame_at_sec)
            if not ok:
                placeholder = out_dir / "_frame.png"
                _write_placeholder(placeholder)
                src = placeholder
                produced["placeholder"] = placeholder.name

        try:
            from PIL import Image, ImageOps
        except ImportError:
            return {"status": "skipped", "reason": "pillow not installed"}

        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode in ("P", "LA"):
                    im = im.convert("RGBA")
                if im.mode != "RGB":
                    im = im.convert("RGB")
                width, height = im.size
                for size_key in sizes:
                    max_edge = _SIZES[size_key]
                    im2 = im.copy()
                    im2.thumbnail((max_edge, max_edge), Image.LANCZOS)
                    dest = out_dir / f"{size_key}.jpg"
                    im2.save(dest, "JPEG", quality=params.quality, optimize=True)
                    produced[size_key] = dest.name
                meta = {"width": width, "height": height, "src_name": src.name}
        except Exception as exc:  # noqa: BLE001  — any image decode error is a skip, not a failure
            return {"status": "skipped", "reason": f"decode error: {exc}"}

        return {
            "status": "ok",
            "sizes": produced,
            "width": meta["width"],
            "height": meta["height"],
            "source": "video-frame" if asset.media_type == MediaType.VIDEO.value else "image",
            "src_name": meta["src_name"],
        }

    def _video_frame(self, src: Path, out_dir: Path, at_sec: float) -> tuple[bool, Path]:
        """Extract a single frame with ffmpeg; return (ok, frame_path).

        Falls back to the source path when ffmpeg is unavailable so the caller
        can still attempt a decode (rare for video in Pillow) or write a
        placeholder.
        """
        dest = out_dir / "_frame.jpg"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-ss", f"{at_sec:.3f}", "-i", str(src),
                    "-frames:v", "1", "-q:v", "3", str(dest),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            return False, src
        return dest.is_file(), dest


def _write_placeholder(path: Path) -> None:
    """Deterministic dark tile with a play glyph — real image data, cheap."""
    from PIL import Image, ImageDraw

    w = h = _DEFAULT_MAX_SIZE
    im = Image.new("RGB", (w, h), (28, 28, 34))
    d = ImageDraw.Draw(im)
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            if (x // 8 + y // 8) % 2:
                d.rectangle([x, y, x + 7, y + 7], fill=(38, 38, 46))
    d.polygon([(w // 2 - 26, h // 2 - 34), (w // 2 - 26, h // 2 + 34), (w // 2 + 40, h // 2)], fill=(220, 220, 226))
    im.save(path, "PNG")
