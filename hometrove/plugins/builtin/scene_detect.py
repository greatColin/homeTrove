"""``basic.scene_detect`` plugin (M1-3).

Detects shot changes in videos with PySceneDetect (ContentDetector) using the
PyAV backend, so no system ffmpeg is required. Emits scene ranges in seconds
plus per-scene keyframe timestamps for M1-4 / M1-5 to reuse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin


class SceneDetectPlugin(BasePlugin):
    id: str = "basic.scene_detect"
    name: str = "视频场景切分"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.VIDEO.value}
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        threshold: float = 27.0
        min_scene_len: float = 0.6   # seconds — ignore cuts closer than this
        backend: str = "pyav"
        include_keyframes: bool = True

    def estimate(self, asset: AssetLike) -> Cost:
        # Video duration is unknown here; charge a fixed probing cost and let
        # calibration refine it after the first run.
        return Cost(seconds=2.0, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: SceneDetectPlugin.ParamsModel = ctx.params  # type: ignore[assignment]

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
            from scenedetect import ContentDetector, detect
        except ImportError:
            return {"status": "skipped", "reason": "scenedetect not installed"}

        try:
            scenes = detect(
                str(src),
                ContentDetector(
                    threshold=params.threshold,
                    min_scene_len=params.min_scene_len,
                ),
                backend=params.backend,
                show_progress=False,
            )
        except Exception as exc:  # noqa: BLE001  — undecodable video => skip
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

        out_scenes: list[dict[str, Any]] = []
        for start, end in scenes:
            start_sec = round(float(start.seconds), 3)
            end_sec = round(float(end.seconds), 3)
            item: dict[str, Any] = {"start": start_sec, "end": end_sec}
            if params.include_keyframes:
                item["keyframe"] = round((start_sec + end_sec) / 2, 3)
            out_scenes.append(item)

        return {
            "status": "ok",
            "scene_count": len(out_scenes),
            "scenes": out_scenes,
        }
