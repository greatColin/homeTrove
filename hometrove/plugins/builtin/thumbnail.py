"""``thumbnail`` plugin (M1-1).

Generates downscaled JPEG copies of an asset into ``{data_dir}/thumbs/{asset_id}/``
(unencrypted) or ``{data_dir}/vault/t/{asset_id}/{size}.c9r`` (vault mode).

* images are resized with Pillow (keeps EXIF orientation, no upscale);
* videos get a representative frame via PyAV (``av`` ships its own bundled
  FFmpeg libraries, so no system ffmpeg is required); when the video cannot be
  decoded a deterministic placeholder PNG is written so the grid never shows a
  broken image for video rows.

The plugin is *not* a failure when it cannot produce a real thumbnail (e.g.
unsupported format, undecodable video): it records a ``skipped`` result so the
frontend falls back to the original file / a labeled tile.
"""

from __future__ import annotations

import io
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

# Fixed size buckets. Keys are the URL query value for ``/api/assets/{id}/thumbnail``.
_SIZES = {
    "small": 320,
    "medium": 1280,
}

_DEFAULT_MAX_SIZE = _SIZES["small"]


class ThumbnailPlugin(BasePlugin):
    id: str = "thumbnail"
    name: str = "缩略图"
    description: str = "生成封面/列表/详情三档缩略图；图片直接缩放，视频抽取首帧（可配时间点）作为封面"
    version: str = "0.2.0"
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

        from hometrove.vault.state import VaultStatus, get_state

        vault_state = get_state()
        use_vault = vault_state.status == VaultStatus.UNLOCKED
        if vault_state.status == VaultStatus.LOCKED:
            return {"status": "skipped", "reason": "vault is locked"}

        tmp_src: Path | None = None

        src = resolve_asset_path(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        want = [s for s in params.sizes if s in _SIZES]
        sizes = want or [_DEFAULT_MAX_SIZE]
        produced: dict[str, str] = {}
        is_video = asset.media_type == MediaType.VIDEO.value

        try:
            from PIL import Image, ImageOps
        except ImportError:
            return {"status": "skipped", "reason": "pillow not installed"}

        # For videos, first pull a representative frame as a JPEG with PyAV.
        frame_path: Path | None = None
        if is_video:
            out_dir = ctx.data_dir / "thumbs" / str(asset.id)
            out_dir.mkdir(parents=True, exist_ok=True)
            frame_path = self._video_frame(src, out_dir, params.video_frame_at_sec)
            if frame_path is None:
                placeholder = out_dir / "_frame.png"
                _write_placeholder(placeholder)
                produced["placeholder"] = placeholder.name
                frame_path = placeholder
            src_for_decode = frame_path
        else:
            src_for_decode = src

        try:
            with Image.open(src_for_decode) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode in ("P", "LA"):
                    im = im.convert("RGBA")
                if im.mode != "RGB":
                    im = im.convert("RGB")
                width, height = im.size
                meta = {"width": width, "height": height, "src_name": src_for_decode.name}
                for size_key in sizes:
                    max_edge = _SIZES[size_key]
                    im2 = im.copy()
                    im2.thumbnail((max_edge, max_edge), Image.LANCZOS)
                    buf = io.BytesIO()
                    im2.save(buf, "JPEG", quality=params.quality, optimize=True)
                    payload = buf.getvalue()
                    if use_vault:
                        from hometrove.vault.paths import vault_thumbnail_path
                        from hometrove.vault.stream import encrypt_bytes

                        dest = vault_thumbnail_path(ctx.data_dir, asset.id, size_key)
                        encrypt_bytes(
                            payload,
                            dest,
                            key=bytes(vault_state.subkeys.content_enc_key),
                            asset_id=asset.id,
                        )
                        produced[size_key] = dest.name
                    else:
                        out_dir = ctx.data_dir / "thumbs" / str(asset.id)
                        out_dir.mkdir(parents=True, exist_ok=True)
                        dest = out_dir / f"{size_key}.jpg"
                        dest.write_bytes(payload)
                        produced[size_key] = dest.name
        except Exception as exc:  # noqa: BLE001  — any image decode error is a skip, not a failure
            if tmp_src and tmp_src.exists():
                tmp_src.unlink(missing_ok=True)
            return {"status": "skipped", "reason": f"decode error: {exc}"}

        if tmp_src and tmp_src.exists():
            tmp_src.unlink(missing_ok=True)

        return {
            "status": "ok",
            "sizes": produced,
            "width": meta["width"],
            "height": meta["height"],
            "source": "video-frame" if asset.media_type == MediaType.VIDEO.value else "image",
            "src_name": meta["src_name"],
            "vault": use_vault,
        }

    def _video_frame(self, src: Path, out_dir: Path, at_sec: float) -> Path | None:
        """Extract a single frame with PyAV (bundled FFmpeg); return path or None.

        Returns ``None`` when the video cannot be decoded so the caller can
        write a placeholder instead of failing the job.
        """
        dest = out_dir / "_frame.jpg"
        try:
            import av

            with av.open(str(src)) as container:
                try:
                    container.seek(int(at_sec * 1000000))
                except (ValueError, av.error.FFmpegError, av.AVError):
                    container.seek(0)
                frame = next(container.decode(video=0))
        except Exception:  # noqa: BLE001  — any decode problem => placeholder
            return None

        try:
            from PIL import Image
            import numpy as np

            arr = frame.to_ndarray(format="rgb24")
            Image.fromarray(arr).save(dest, "JPEG", quality=85)
        except Exception:  # noqa: BLE001
            return None
        return dest if dest.is_file() else None


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