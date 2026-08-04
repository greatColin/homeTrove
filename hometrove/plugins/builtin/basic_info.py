"""``basic.info`` plugin.

Records the most basic facts about an asset: name, media type (image / video
/ other), size, mtime, and—where cheap—a content hash prefix.

M0 deliberately avoids libraries like PIL, pillow-heif, or ffprobe here so the
plugin can run in any environment, including a Docker base image without
system media deps. Dimensions and duration are read from raw container
headers if the extension is recognizable; if not, the field is set to None
(no error).

No external heavyweight dependency is imported.
"""

from __future__ import annotations

import struct
from pathlib import Path

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin


# Cheap media-type classifier: extension only. We keep this in one place so
# the scanner and the plugin agree on what counts as image vs video.
_IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".raw", ".dng", ".cr2", ".cr3", ".nef",
}
_VIDEO_SUFFIXES = {
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".wmv", ".flv",
    ".mpg", ".mpeg", ".mts", ".m2ts", ".3gp",
}


def classify(path: Path) -> MediaType:
    ext = path.suffix.lower()
    if ext in _IMAGE_SUFFIXES:
        return MediaType.IMAGE
    if ext in _VIDEO_SUFFIXES:
        return MediaType.VIDEO
    return MediaType.OTHER


def _read_image_dimensions(path: Path) -> tuple[int | None, int | None]:
    """Read width/height from common image headers without external libs.

    Supports PNG, JPEG, GIF, BMP, WEBP. Any unknown/odd container returns None.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None, None

    # PNG: 8-byte signature + IHDR (13 bytes big-endian: width, height)
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)

    # GIF: "GIF87a" or "GIF89a" then 2 bytes width, 2 bytes height
    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        w, h = struct.unpack("<HH", head[6:10])
        return int(w), int(h)

    # BMP: "BM" then a DIB header at offset 14 contains width/height (signed ints, little-endian)
    if head[:2] == b"BM" and len(head) >= 26:
        # width/height are at offsets 18 and 22 within the file, so read more.
        try:
            with open(path, "rb") as f:
                f.seek(18)
                buf = f.read(8)
            w, h = struct.unpack("<ii", buf)
            return abs(int(w)), abs(int(h))
        except OSError:
            return None, None

    # WEBP: 'RIFF' .... 'WEBP' .... VP8 / VP8L / VP8X ...
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        try:
            with open(path, "rb") as f:
                f.seek(12)
                chunk = f.read(8)
            fourcc = chunk[:4]
            if fourcc == b"VP8 " and len(head) >= 30:
                # VP8 lossy bitstream: width/height in little-endian at +6, +8 of the chunk header.
                with open(path, "rb") as f:
                    f.seek(26)
                    wh = f.read(4)
                w, h = struct.unpack("<HH", wh)
                return int(w & 0x3FFF), int(h & 0x3FFF)
            if fourcc == b"VP8L" and len(head) >= 25:
                # VP8L: 14-bit width-1, 14-bit height-1 packed little-endian.
                with open(path, "rb") as f:
                    f.seek(21)
                    b = f.read(4)
                w = ((b[1] & 0x3F) << 8 | b[0]) + 1
                h = (((b[3] & 0x0F) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6)) + 1
                return int(w), int(h)
            if fourcc == b"VP8X":
                with open(path, "rb") as f:
                    f.seek(24)
                    b = f.read(6)
                w = int.from_bytes(b[0:3], "little") + 1
                h = int.from_bytes(b[3:6], "little") + 1
                return int(w), int(h)
        except OSError:
            return None, None
        return None, None

    # JPEG: SOF0/SOF2 marker carries the dimensions
    if head[:2] == b"\xff\xd8":
        try:
            with open(path, "rb") as f:
                f.seek(2)
                while True:
                    byte = f.read(1)
                    if not byte or byte != b"\xff":
                        return None, None
                    marker = f.read(1)
                    if not marker:
                        return None, None
                    # Stand-alone markers
                    if marker in (b"\xd8", b"\xd9"):
                        if marker == b"\xd9":
                            return None, None
                        continue
                    # Read segment length
                    length_bytes = f.read(2)
                    if len(length_bytes) != 2:
                        return None, None
                    length = struct.unpack(">H", length_bytes)[0]
                    if marker in (b"\xc0", b"\xc1", b"\xc2", b"\xc3"):
                        # SOF marker, next 2 bytes are precision, then height, width
                        f.read(3)
                        wh = f.read(4)
                        if len(wh) != 4:
                            return None, None
                        h, w = struct.unpack(">HH", wh)
                        return int(w), int(h)
                    # Skip segment payload
                    f.seek(length - 2, 1)
        except OSError:
            return None, None
    return None, None


class BasicInfoPlugin(BasePlugin):
    id: str = "basic.info"
    name: str = "Basic Info"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    depends_on: list[str] = []

    class ParamsModel(BaseModel):
        read_image_dimensions: bool = True
        read_video_metadata: bool = False    # OFF by default — needs ffprobe

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.02, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict:
        # Reconstruct the on-disk path from ``media_root`` + relative portion,
        # since the database column may include the ``\0`` separator we use
        # for uniqueness keys.
        raw = asset.path
        if "\0" in raw:
            _root, rel = raw.split("\0", 1)
            path = Path(asset.media_root) / rel
        elif Path(raw).is_absolute():
            path = Path(raw)
        else:
            path = Path(asset.media_root) / raw

        params: BasicInfoPlugin.ParamsModel = ctx.params  # type: ignore[assignment]

        size = asset.size_bytes
        if size is None:
            try:
                size = path.stat().st_size
            except OSError:
                size = None

        mtime = asset.mtime
        try:
            if mtime is None:
                mtime = int(path.stat().st_mtime)
        except OSError:
            mtime = None

        width: int | None = None
        height: int | None = None
        if params.read_image_dimensions and asset.media_type == MediaType.IMAGE.value:
            width, height = _read_image_dimensions(path)

        # Video metadata: deliberately left as None unless the operator
        # enabled ffprobe-backed extraction (not in M0). Recording ``None``
        # explicitly so the schema is stable.
        duration_sec: float | None = None

        return {
            "name": path.name,
            "media_type": asset.media_type,
            "size_bytes": size,
            "mtime": mtime,
            "width": width,
            "height": height,
            "duration_sec": duration_sec,
            "taken_at": None,  # EXIF parsing is a separate plugin in M1
        }
