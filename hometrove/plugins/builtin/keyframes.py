"""``basic.keyframes`` — per-scene keyframe extraction (v1).

For each video scene detected by ``basic.scene_detect``, extract
``per_scene`` keyframes (JPEG) into ``{data_dir}/keyframes/{asset_id}/`` and
record per-frame timestamps + paths in the plugin result. The frontend uses
this to render a keyframe filmstrip and jump the player to a scene's
representative moment.

Reuses the shared decode cache via ``PluginContext.frames(at_seconds=...)``
so scene vectors (``embedding.jina_clip``) and thumbnails do not re-decode the
same video. When scene detection produced nothing, the whole video is treated
as a single window. Undecodable videos record ``skipped``, never ``failed``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext, resolve_asset_path
from hometrove.plugins.base import BasePlugin


class KeyframesPlugin(BasePlugin):
    id: str = "basic.keyframes"
    name: str = "视频关键帧"
    description: str = "仅处理视频：按场景切分结果抽取代表帧 JPEG，落地到磁盘供详情页条带展示和跳秒"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.VIDEO.value}
    # Scene boundaries come from scene_detect; its skipped result for a
    # non-video asset satisfies the dependency check, but we only run on video.
    depends_on: list[str] = ["basic.info", "basic.scene_detect"]

    class ParamsModel(BaseModel):
        per_scene: int = 1    # keyframes per scene
        max_scenes: int = 48  # cap scenes processed per video
        max_side: int = 640   # longest edge of the output JPEG
        quality: int = 85

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.3, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: KeyframesPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        if asset.media_type != MediaType.VIDEO.value:
            return {"status": "skipped", "reason": "not a video asset"}
        if ctx.data_dir is None:
            return {"status": "skipped", "reason": "no data_dir in context"}

        from hometrove.vault.state import VaultStatus, get_state

        vault_state = get_state()
        use_vault = vault_state.status == VaultStatus.UNLOCKED
        if vault_state.status == VaultStatus.LOCKED:
            return {"status": "skipped", "reason": "vault is locked"}

        src = resolve_asset_path(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        scenes = self._scenes(ctx, params.max_scenes)
        times, windows = self._sample_times(scenes, params.per_scene)

        try:
            frames = ctx.frames(at_seconds=times)
        except Exception:  # noqa: BLE001  — undecodable => skip
            return {"status": "skipped", "reason": "video decode failed"}

        if not frames:
            return {"status": "skipped", "reason": "video decode returned no frames"}

        keyframes: list[dict[str, Any]] = []
        total = 0
        for (scene_idx, idx, t_sec, t_start, t_end), frame in zip(windows, frames):
            if frame is None:
                continue
            name = f"scene-{scene_idx}-{idx}.jpg"
            if use_vault:
                from hometrove.vault.paths import vault_keyframe_path
                from hometrove.vault.stream import encrypt_bytes

                dest = vault_keyframe_path(ctx.data_dir, asset.id, scene_idx, idx)
                buf = self._frame_to_jpeg_bytes(frame, params.max_side, params.quality)
                if buf is None:
                    continue
                encrypt_bytes(
                    buf,
                    dest,
                    key=bytes(vault_state.subkeys.content_enc_key),
                    asset_id=asset.id,
                )
                keyframes.append(
                    {
                        "scene": scene_idx,
                        "index": idx,
                        "t_sec": round(t_sec, 3),
                        "t_start": round(t_start, 3),
                        "t_end": round(t_end, 3),
                        "file": dest.name,
                    }
                )
                total += 1
                continue

            out_dir = ctx.data_dir / "keyframes" / str(asset.id)
            out_dir.mkdir(parents=True, exist_ok=True)
            if self._save_jpeg(frame, out_dir / name, params.max_side, params.quality):
                keyframes.append(
                    {
                        "scene": scene_idx,
                        "index": idx,
                        "t_sec": round(t_sec, 3),
                        "t_start": round(t_start, 3),
                        "t_end": round(t_end, 3),
                        "file": name,
                    }
                )
                total += 1

        return {
            "status": "ok",
            "scene_count": len(scenes),
            "per_scene": params.per_scene,
            "total": total,
            "keyframes": keyframes,
            "vault": use_vault,
        }

    # ----- helpers -----

    def _scenes(self, ctx: PluginContext, max_scenes: int) -> list[dict[str, Any]]:
        """Scene list from scene_detect, bounded to ``max_scenes``."""
        data = ctx.result_of("basic.scene_detect")
        if not data:
            return []
        scenes = data.get("scenes") or []
        return scenes[:max_scenes]

    def _sample_times(
        self,
        scenes: list[dict[str, Any]],
        per_scene: int,
    ) -> tuple[list[float], list[tuple[int, int, float, float, float]]]:
        """Compute decode timestamps and their scene windows.

        Returns ``(times, windows)`` where each window is
        ``(scene_idx, idx, t_sec, t_start, t_end)``. Without scene data a
        single window ``[0, 0]`` is used (a single frame at 0s — the cover).
        """
        count = max(1, per_scene)
        times: list[float] = []
        windows: list[tuple[int, int, float, float, float]] = []

        if not scenes:
            for idx in range(count):
                t = 0.0
                times.append(t)
                windows.append((0, idx, t, 0.0, 0.0))
            return times, windows

        for scene_idx, scene in enumerate(scenes):
            start = float(scene.get("start") or 0.0)
            end = float(scene.get("end") or start)
            if end <= start:
                end = start + 1e-3
            keyframe = float(scene.get("keyframe") or ((start + end) / 2.0))
            if count == 1:
                t = max(start, min(keyframe, end))
                times.append(t)
                windows.append((scene_idx, 0, t, start, end))
                continue
            for idx in range(count):
                t = start + (end - start) * (idx + 0.5) / count
                t = max(start, min(t, end))
                times.append(t)
                windows.append((scene_idx, idx, t, start, end))
        return times, windows

    def _save_jpeg(self, frame: Any, dest: Path, max_side: int, quality: int) -> bool:
        """Downscale a numpy RGB frame to JPEG; True on success."""
        try:
            from PIL import Image

            im = Image.fromarray(frame)
            im.thumbnail((max_side, max_side), Image.LANCZOS)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.save(dest, "JPEG", quality=quality, optimize=True)
        except Exception:  # noqa: BLE001
            return False
        return dest.is_file()

    def _frame_to_jpeg_bytes(self, frame: Any, max_side: int, quality: int) -> Optional[bytes]:
        """Encode a numpy frame to JPEG bytes for vault writing."""

        import io

        try:
            from PIL import Image

            im = Image.fromarray(frame)
            im.thumbnail((max_side, max_side), Image.LANCZOS)
            if im.mode != "RGB":
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True)
        except Exception:  # noqa: BLE001
            return None
        return buf.getvalue()


__all__ = ["KeyframesPlugin"]
