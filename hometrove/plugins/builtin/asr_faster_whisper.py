"""``asr.faster_whisper`` — speech-to-text with timestamps (M1-10).

Audio comes from two places:

* videos: a single audio track is demuxed with PyAV (ships with bundled
  FFmpeg libs, no system ``ffmpeg`` needed). The track is decoded to
  mono 16 kHz PCM and fed to faster-whisper in 30 s chunks.
* (audio-only sources are not scanned today — extending this plugin when
  M0's scanner grows audio support is a one-liner.)

The transcription backend has two implementations behind the same
contract:

* **real** — ``faster_whisper.WhisperModel`` if the package is importable
  and the configured model weight is available. CPU float32 is used; the
  v1.1 GPU path is a ``params.compute_type`` switch away.
* **mock** — a deterministic pseudo-segmenter that emits synthetic cues
  derived from the source file. This is the default when faster-whisper
  is missing so the schema, search, and detail UI can be exercised
  end-to-end on a vanilla install.

Output rows go into ``asr_transcripts`` (one row per segment), separate
from the plugin's own ``plugin_results`` row (summary + status). The
search keyword path joins the table directly.
"""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from hometrove.plugins.api import (
    AssetLike,
    Cost,
    MediaType,
    PluginContext,
    resolve_asset_path,
)
from hometrove.plugins.base import BasePlugin

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Real-backend cache: faster-whisper is heavy and pinned to one model per
# process. Keep a single ``WhisperModel`` instance; ``shutdown()`` releases it.
# ---------------------------------------------------------------------------
_WHISPER = None
_WHISPER_LOCK = threading.Lock()


def _release_whisper() -> None:
    global _WHISPER
    with _WHISPER_LOCK:
        _WHISPER = None


def _get_whisper(model_size: str, compute_type: str):  # noqa: ANN202
    """Lazily construct / reuse a ``WhisperModel``. Returns ``None`` if the
    package isn't installed or construction fails (e.g. model not
    downloaded)."""
    global _WHISPER
    if _WHISPER is not None and _WHISPER[0] == (model_size, compute_type):
        return _WHISPER[1]
    with _WHISPER_LOCK:
        if _WHISPER is not None and _WHISPER[0] == (model_size, compute_type):
            return _WHISPER[1]
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            inst = WhisperModel(model_size, device="cpu", compute_type=compute_type)
        except Exception:  # noqa: BLE001  — model not downloaded, OOM, etc.
            return None
        _WHISPER = ((model_size, compute_type), inst)
        return inst


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------


def _extract_audio(src: Path, out_wav: Path) -> Optional[float]:
    """Demux the first audio stream from ``src`` into a mono 16 kHz WAV.

    Returns the source duration in seconds (best effort), or ``None`` when
    no audio stream could be found / decoded. ``out_wav`` is always
    created by PyAV when audio exists, so callers can rely on
    ``out_wav.is_file()`` afterwards.
    """
    import av

    duration: Optional[float] = None
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    try:
        with av.open(str(src)) as container:
            if container.duration:
                duration = float(container.duration) / 1_000_000
            audio = next(
                (s for s in container.streams if s.type == "audio"),
                None,
            )
            if audio is None:
                return None
            with av.open(str(out_wav), mode="w", format="wav") as out:
                stream = out.add_stream("pcm_s16le", rate=16_000)
                stream.layout = "mono"
                resampler = av.AudioResampler(
                    format="s16", layout="mono", rate=16_000,
                )
                for frame in container.decode(audio):
                    out_packet = resampler.resample(frame)
                    if out_packet is None:
                        continue
                    for p in (out_packet if isinstance(out_packet, list) else [out_packet]):
                        for pkt in stream.encode(p):
                            out.mux(pkt)
                for pkt in stream.encode(None):
                    out.mux(pkt)
    except Exception:  # noqa: BLE001  — any decode/demux failure
        return None
    if not out_wav.is_file() or out_wav.stat().st_size == 0:
        return None
    return duration


# ---------------------------------------------------------------------------
# Real backend: faster-whisper
# ---------------------------------------------------------------------------


def _transcribe_real(
    wav_path: Path,
    *,
    language: Optional[str],
    beam_size: int,
) -> Optional[list[dict[str, Any]]]:
    inst = _get_whisper(_MODEL_SIZE, _COMPUTE_TYPE)
    if inst is None:
        return None
    try:
        segments, _info = inst.transcribe(
            str(wav_path),
            language=language,
            beam_size=beam_size,
            vad_filter=False,
        )
        out: list[dict[str, Any]] = []
        for seg in segments:
            txt = (seg.text or "").strip()
            if not txt:
                continue
            out.append(
                {
                    "t_start": round(float(seg.start), 3),
                    "t_end": round(float(seg.end), 3),
                    "text": txt,
                    "lang": getattr(seg, "language", language) or language,
                    "confidence": round(float(getattr(seg, "avg_logprob", 0.0) or 0.0), 4),
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001  — model load / decode failure
        log.warning("asr.faster_whisper: real transcription failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Mock backend: deterministic pseudo-cues derived from the source file.
# This keeps the schema, detail UI, and search path exercisable on a
# vanilla install (no model download).
# ---------------------------------------------------------------------------


_MOCK_PHRASES = (
    "这是一段测试语音，用于演示自动字幕功能。",
    "家庭影像里的声音也是记忆的一部分。",
    "系统会自动识别并打上时间戳。",
    "Mock transcript keeps the pipeline observable before the real model lands.",
    "You can search for words spoken in the video.",
    "海边的风，院子里的笑声，孩子的第一句话。",
)


def _transcribe_mock(src: Path, duration: float) -> list[dict[str, Any]]:
    """Emit 2–4 fake segments spanning roughly the clip length.

    Timing is anchored to ``duration`` (from ``_extract_audio``) and the
    text is rotated by a hash of the file path so the same file always
    yields the same transcript.
    """
    if duration <= 0:
        duration = 5.0
    seed = int(hashlib.sha256(str(src).encode()).hexdigest(), 16)
    n = 2 + seed % 3  # 2..4
    span = max(duration / n, 1.0)
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "t_start": round(i * span, 3),
                "t_end": round(min(duration, (i + 1) * span), 3),
                "text": _MOCK_PHRASES[(seed // (i + 1)) % len(_MOCK_PHRASES)],
                "lang": "zh",
                "confidence": round(0.7 + (seed % 13) / 100.0, 4),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

# Module-level defaults used when ``ParamsModel`` doesn't override them.
# Exposed as constants so tests can introspect the runtime defaults.
_MODEL_SIZE = "small"
_COMPUTE_TYPE = "int8"


class AsrFasterWhisperPlugin(BasePlugin):
    id: str = "asr.faster_whisper"
    name: str = "语音转写（faster-whisper）"
    description: str = "仅处理视频：抽取音频转写为带时间戳的字幕文本，写入 asr_transcripts 表用于搜索和跳秒"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.VIDEO.value}
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        language: Optional[str] = None       # auto-detect when None
        beam_size: int = 3
        # ``backend`` lets the operator pin a backend regardless of
        # whether the real model is installed. ``auto`` prefers the real
        # model when available and falls back to mock; ``mock`` forces the
        # synthetic path; ``faster_whisper`` requires the package.
        backend: str = "auto"
        # Confidence floor: segments below this are dropped from the
        # transcripts table. Mock segments already exceed it; real
        # segments occasionally fall under it (e.g. music-only cues).
        min_confidence: float = -1.0  # default = keep all (mock floors at 0.7)

    def estimate(self, asset: AssetLike) -> Cost:
        # Heuristic: ~30 s of CPU per minute of audio for the small model on
        # CPU int8. The estimate is intentionally conservative — actual
        # cost is calibrated after the first run via the worker.
        if asset.media_type == MediaType.VIDEO.value:
            return Cost(seconds=15.0, device="cpu")
        return Cost(seconds=0.0, device="cpu")

    def shutdown(self) -> None:
        _release_whisper()

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: AsrFasterWhisperPlugin.ParamsModel = ctx.params  # type: ignore[assignment]

        if asset.media_type != MediaType.VIDEO.value:
            return {"status": "skipped", "reason": "not a video asset"}

        src = resolve_asset_path(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        if ctx.db is None:
            return {"status": "skipped", "reason": "no database context"}

        tmp_dir = ctx.temp_dir()
        wav_path = tmp_dir / "audio.wav"
        duration = _extract_audio(src, wav_path)
        if duration is None or not wav_path.is_file():
            return {"status": "skipped", "reason": "no audio track"}

        backend = params.backend.lower()
        segments: Optional[list[dict[str, Any]]]
        backend_used = "faster_whisper"
        if backend == "mock":
            segments = _transcribe_mock(src, duration)
            backend_used = "mock"
        elif backend == "faster_whisper":
            segments = _transcribe_real(
                wav_path,
                language=params.language,
                beam_size=params.beam_size,
            )
            if segments is None:
                return {
                    "status": "skipped",
                    "reason": "faster_whisper unavailable (pip install faster-whisper)",
                }
        else:  # 'auto'
            segments = _transcribe_real(
                wav_path,
                language=params.language,
                beam_size=params.beam_size,
            )
            if segments is None:
                segments = _transcribe_mock(src, duration)
                backend_used = "mock"

        segments = [s for s in (segments or []) if s["t_end"] > s["t_start"]]
        if params.min_confidence > -1.0:
            segments = [
                s for s in segments if (s.get("confidence") or 0.0) >= params.min_confidence
            ]
        if not segments:
            return {"status": "ok", "segments": 0, "backend": backend_used}

        self._store(asset, ctx, segments)
        return {
            "status": "ok",
            "segments": len(segments),
            "backend": backend_used,
            "lang": segments[0].get("lang"),
            "duration_sec": round(duration, 3),
        }

    def _store(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        segments: list[dict[str, Any]],
    ) -> None:
        from hometrove.models import AsrTranscript

        assert ctx.db is not None
        session = ctx.db
        # Idempotent: drop this plugin's rows for the asset first so
        # re-running replaces the version's data rather than stacking.
        from sqlalchemy import delete

        session.execute(
            delete(AsrTranscript).where(
                AsrTranscript.asset_id == asset.id,
                AsrTranscript.plugin_id == self.id,
                AsrTranscript.plugin_version == self.version,
            )
        )
        rows = [
            AsrTranscript(
                asset_id=asset.id,
                plugin_id=self.id,
                plugin_version=self.version,
                t_start=float(s["t_start"]),
                t_end=float(s["t_end"]),
                text=str(s.get("text") or "").strip(),
                lang=s.get("lang"),
                confidence=s.get("confidence"),
            )
            for s in segments
        ]
        rows = [r for r in rows if r.text]
        if rows:
            session.add_all(rows)
        session.flush()


__all__ = ["AsrFasterWhisperPlugin"]


def probe_audio(src: Path) -> Optional[float]:  # pragma: no cover — diagnostic helper
    """Standalone helper used by diagnostic scripts; returns audio duration.

    Kept in the module so an operator can do
    ``python -c "from hometrove.plugins.builtin.asr_faster_whisper import probe_audio; print(probe_audio(p))"``
    without importing the plugin class.
    """
    tmp = Path(tempfile.mkdtemp(prefix="hometrove-asr-probe-"))
    try:
        wav = tmp / "audio.wav"
        return _extract_audio(src, wav)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# Marker used by tests to skip behaviour assertions that need audio without
# requiring every test to import the plugin.
_HAS_FASTER_WHISPER: bool | None = None


def has_faster_whisper() -> bool:
    """Cache-checked import probe (no model download, no model load)."""
    global _HAS_FASTER_WHISPER
    if _HAS_FASTER_WHISPER is None:
        try:
            import faster_whisper  # noqa: F401
            _HAS_FASTER_WHISPER = True
        except ImportError:
            _HAS_FASTER_WHISPER = False
    return _HAS_FASTER_WHISPER