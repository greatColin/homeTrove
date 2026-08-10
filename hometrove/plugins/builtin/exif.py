"""``exif`` plugin (M1-2, pure Python).

Reads metadata with zero external software:

* **images**: Pillow reads the EXIF block (Make/Model/ISO/exposure/GPS/…);
* **videos**: PyAV (ships bundled FFmpeg libs) reports duration, codec,
  resolution, rotation and container metadata.

Geolocation, when present, is normalised into ``gps_lat`` / ``gps_lon`` decimal
degrees. Undecodable assets are recorded as ``skipped`` (never a failure).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hometrove.plugins.api import (
    AssetLike,
    Cost,
    MediaType,
    PluginContext,
    resolve_asset_path,
)
from hometrove.plugins.base import BasePlugin

# Pillow EXIF tag ids we surface, mapped to stable output keys.
_TAG_IDS = {
    0x010F: "make",
    0x0110: "model",
    0x0132: "modify_date",
    0x0131: "software",
    0x013B: "artist",
    0x8298: "copyright",
    0x8827: "iso",
    0x829A: "exposure_time",
    0x829D: "exposure_mode",
    0x920A: "lens",
    0x9209: "flash",
    0xA002: "pixel_width",
    0xA003: "pixel_height",
    0xA405: "focal_length_35mm",
    0xA434: "lens",
}

# Fields that only make sense for videos; gathered from PyAV.
_VIDEO_KEYS = ("duration_sec", "codec", "video_width", "video_height",
               "fps", "rotation", "encoder", "mime_type")

_MAKE_IFD = 0x010F
_MODEL_IFD = 0x0110


class ExifPlugin(BasePlugin):
    id: str = "exif"
    name: str = "EXIF 元数据"
    description: str = "读取图片 EXIF（相机型号/ISO/光圈/快门/GPS 等）和视频元数据（编解码器、时长、分辨率、旋转）"
    version: str = "0.2.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        read_geolocation: bool = True
        read_video_metadata: bool = True

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.05, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: ExifPlugin.ParamsModel = ctx.params  # type: ignore[assignment]

        src = resolve_asset_path(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        try:
            if asset.media_type == MediaType.IMAGE.value:
                out = self._image_exif(src)
            elif asset.media_type == MediaType.VIDEO.value:
                out = self._video_meta(src) if params.read_video_metadata else {}
            else:
                return {"status": "skipped", "reason": "unsupported media type"}
        except Exception as exc:  # noqa: BLE001  — any decode problem is a skip
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

        if not params.read_geolocation:
            out.pop("gps_lat", None)
            out.pop("gps_lon", None)

        return {"status": "ok", "metadata": out}

    def _image_exif(self, path: Path) -> dict[str, Any]:
        from PIL import Image

        out: dict[str, Any] = {}
        with Image.open(path) as im:
            exif = im.getexif()
            for tag_id, key in _TAG_IDS.items():
                if tag_id in exif and exif[tag_id] is not None:
                    out[key] = exif[tag_id]
            if _MAKE_IFD in exif:
                out["make"] = exif[_MAKE_IFD]
            if _MODEL_IFD in exif:
                out["model"] = exif[_MODEL_IFD]
            if "iso" in out:
                out["iso"] = int(out["iso"])
            # Normalise GPS to decimal degrees.
            if 0x8825 in exif:
                gps = exif.get_ifd(0x8825)
                lat = _gps_deg(gps, 0x0002, 0x0001, 0x0003)
                lon = _gps_deg(gps, 0x0004, 0x0003, 0x0001)
                if lat is not None:
                    out["gps_lat"] = lat
                if lon is not None:
                    out["gps_lon"] = lon
        return out

    def _video_meta(self, path: Path) -> dict[str, Any]:
        import av

        out: dict[str, Any] = {}
        with av.open(str(path)) as container:
            if container.duration:
                out["duration_sec"] = round(container.duration / 1_000_000, 3)
            if container.streams.video:
                vs = container.streams.video[0]
                if vs.width and vs.height:
                    out["video_width"] = vs.width
                    out["video_height"] = vs.height
                if vs.average_rate:
                    out["fps"] = round(float(vs.average_rate), 3)
                if vs.codec_context and vs.codec_context.name:
                    out["codec"] = vs.codec_context.name
                if vs.metadata and vs.metadata.get("rotate"):
                    out["rotation"] = int(vs.metadata["rotate"])
            if container.metadata:
                for k in ("encoder", "creation_time"):
                    if container.metadata.get(k):
                        out[k] = container.metadata[k]
        return out


def _gps_deg(gps: dict, lat_lon_tag: int, ref_tag: int, alt_ref_tag: int) -> float | None:
    """Convert a GPS IFD coordinate (tag 1/2 with ref tag) to decimal degrees."""
    import fractions

    coord = gps.get(lat_lon_tag)
    if coord is None:
        return None
    try:
        if not isinstance(coord, (tuple, list)):
            return None
        d, m, s = (float(fractions.Fraction(str(x))) for x in coord)
        deg = d + m / 60.0 + s / 3600.0
    except (TypeError, ValueError, ZeroDivisionError, AttributeError):
        return None
    ref = gps.get(ref_tag)
    if ref in ("S", "W"):
        deg = -deg
    return round(deg, 6)
