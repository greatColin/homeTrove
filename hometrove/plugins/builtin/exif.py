"""``exif`` plugin (M1-2).

Reads metadata (camera, lens, ISO, exposure, GPS, capture time) with a
persistent exiftool process (``-stay_open``) so repeated calls skip process
spawn cost. Output uses plain group-less keys so the frontend detail page can
render the JSON directly.

When exiftool is missing the plugin records ``skipped`` (never fails).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin

# Camera/lens/exposure/time/GPS tags worth surfacing, mapped to stable output keys.
_TAGS = {
    "Make": "make",
    "Model": "model",
    "LensModel": "lens",
    "LensMake": "lens_make",
    "ISO": "iso",
    "ExposureTime": "exposure_time",
    "FNumber": "aperture",
    "FocalLength": "focal_length",
    "FocalLengthIn35mmFormat": "focal_length_35mm",
    "ExposureProgram": "exposure_program",
    "ExposureMode": "exposure_mode",
    "WhiteBalance": "white_balance",
    "Flash": "flash",
    "Orientation": "orientation",
    "Software": "software",
    "Artist": "artist",
    "Copyright": "copyright",
    "DateTimeOriginal": "taken_at_original",
    "CreateDate": "create_date",
    "GPSLatitude": "gps_latitude",
    "GPSLongitude": "gps_longitude",
    "GPSAltitude": "gps_altitude",
    "GPSLatitudeRef": "gps_latitude_ref",
    "GPSLongitudeRef": "gps_longitude_ref",
    "GPSDateTime": "gps_datetime",
}


class _ExifTool:
    """Persistent exiftool subprocess (``-stay_open``) with a lock.

    Commands are piped through stdin; responses are read line-by-line. A read
    deadline prevents a wedged process from hanging the worker forever.
    """

    _MAX_IDLE = 30  # seconds without use before we kill the process

    def __init__(self, binary: str) -> None:
        self._binary = binary
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._last_use = 0.0

    def _ensure_proc(self) -> subprocess.Popen:
        import time

        now = time.monotonic()
        if self._proc is not None:
            if self._proc.poll() is None:
                if now - self._last_use > self._MAX_IDLE:
                    self._close()
                else:
                    return self._proc
        self._proc = subprocess.Popen(
            [self._binary, "-stay_open", "True", "-@", "-", "-q", "-j", "-a"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._last_use = now
        return self._proc

    def _close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.write("-stay_open\nFalse\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self._proc.terminate()
            except OSError:
                pass
        self._proc = None

    def extract(self, path: str) -> list[dict]:
        import time

        with self._lock:
            proc = self._ensure_proc()
            self._last_use = time.monotonic()
            try:
                proc.stdin.write(f"-j\n{path}\n-execute\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise RuntimeError(f"exiftool stdin closed: {exc}") from exc

            # ``-j`` prints one JSON array terminated by a line containing ``]``.
            # Read in a helper thread so a wedged exiftool can be timed out
            # instead of hanging the worker forever.
            import queue as _queue

            out: _queue.Queue = _queue.Queue()

            def _read() -> None:
                try:
                    lines: list[str] = []
                    while True:
                        line = proc.stdout.readline()
                        if line == "":
                            raise RuntimeError("exiftool stdout closed")
                        if line.rstrip("\n").strip() == "{ready}":
                            break  # end-of-command marker in -stay_open mode
                        lines.append(line)
                    out.put(json.loads("".join(lines)))
                except Exception as exc:  # noqa: BLE001
                    out.put(exc)

            t = threading.Thread(target=_read, name="exiftool-read", daemon=True)
            t.start()
            t.join(20.0)
            if t.is_alive():
                self._close()  # kill the wedged process so a new one spawns next call
                raise RuntimeError("exiftool read timed out")
            got = out.get()
            if isinstance(got, Exception):
                raise got
        return got


_global_lock = threading.Lock()
_global_tool: Optional[_ExifTool] = None


def _get_tool() -> Optional[_ExifTool]:
    global _global_tool
    binary = shutil.which("exiftool")
    if not binary:
        return None
    with _global_lock:
        if _global_tool is None:
            _global_tool = _ExifTool(binary)
        return _global_tool


class ExifPlugin(BasePlugin):
    id: str = "exif"
    name: str = "EXIF 元数据"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        read_geolocation: bool = True
        include_raw_tags: bool = False

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.05, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: ExifPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        tool = _get_tool()
        if tool is None:
            return {"status": "skipped", "reason": "exiftool not installed"}

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

        try:
            records = tool.extract(str(src))
        except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
            return {"status": "skipped", "reason": f"exiftool error: {exc}"}

        rec = records[0] if records else {}
        out: dict[str, Any] = {}
        for tag, key in _TAGS.items():
            if tag in rec and rec[tag] is not None:
                out[key] = rec[tag]

        if not params.read_geolocation:
            for k in (
                "gps_latitude", "gps_longitude", "gps_altitude",
                "gps_latitude_ref", "gps_longitude_ref", "gps_datetime",
            ):
                out.pop(k, None)

        # Coerce ISO/ExposureTime into numbers where exiftool left strings.
        if "iso" in out:
            try:
                out["iso"] = int(str(out["iso"]))
            except (TypeError, ValueError):
                pass

        if params.include_raw_tags:
            out["raw"] = {k: v for k, v in rec.items() if k not in ("SourceFile",)}

        return {"status": "ok", "metadata": out}
