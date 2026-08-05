"""``face.detect`` — real face detection with InsightFace (SCRFD + ArcFace).

Replaces the M0 ``mock.faces`` simulator. Uses ONNX Runtime on CPU (no GPU
required) and downloads the ``buffalo_l`` model pack on first use. The model
pack is treated like any other model weight — it is the one component not
installable via ``pip``, consistent with the project's "pip install to run,
model weights are downloaded separately" policy.

For images: detect faces on the full frame.
For videos: reads ``basic.scene_detect`` keyframes and runs detection on each,
deduplicating the same person across frames by cosine similarity so one shot
is not counted repeatedly.

Output shape matches what ``face.match`` consumes:
``{"faces": [{"embedding": [...], "confidence": float, "box": [x1,y1,x2,y2]}]}``
"""

from __future__ import annotations

import threading
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

# FaceAnalysis is ~50ms cold import; keep one instance per process.
_APP = None
_APP_LOCK = threading.Lock()


def _get_app() -> Optional[Any]:
    """Lazily build a shared FaceAnalysis instance (thread-safe)."""
    global _APP
    if _APP is not None:
        return _APP
    with _APP_LOCK:
        if _APP is not None:
            return _APP
        try:
            from insightface.app import FaceAnalysis
        except ImportError:
            return None
        try:
            app = FaceAnalysis(
                name="buffalo_l",
                allowed_modules=["detection", "recognition"],
                providers=["CPUExecutionProvider"],
            )
            app.prepare(ctx_id=0, det_size=(640, 640))
            _APP = app
        except Exception:  # noqa: BLE001  — model download / load failure
            return None
    return _APP


def _resolve_src(asset: AssetLike) -> Optional[Any]:
    return resolve_asset_path(asset)


class FaceDetectPlugin(BasePlugin):
    id: str = "face.detect"
    name: str = "人脸检测（InsightFace）"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    # ``basic.scene_detect`` keyframes are consumed opportunistically inside
    # run(); it must not be a DAG dependency because scene_detect only runs on
    # videos and would deadlock image assets.
    depends_on: list[str] = ["basic.info"]

    class ParamsModel(BaseModel):
        det_thresh: float = 0.5
        max_faces: int = 0        # 0 = unlimited
        video_dedup_threshold: float = 0.45
        max_video_frames: int = 8  # cap keyframes processed per video

    def estimate(self, asset: AssetLike) -> Cost:
        # Heavy CNN; images are fast, videos scale with frame count.
        if asset.media_type == MediaType.VIDEO.value:
            return Cost(seconds=3.0, device="cpu")
        return Cost(seconds=0.5, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: FaceDetectPlugin.ParamsModel = ctx.params  # type: ignore[assignment]

        src = _resolve_src(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        app = _get_app()
        if app is None:
            return {
                "status": "skipped",
                "reason": "insightface unavailable (pip install insightface)",
            }

        try:
            if asset.media_type == MediaType.VIDEO.value:
                return self._detect_video(asset, ctx, app, params)
            return self._detect_image(asset, ctx, app, params)
        except Exception as exc:  # noqa: BLE001  — any decode/load failure -> skip
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

    def _detect_image(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        app: Any,
        params: Any,
    ) -> dict[str, Any]:
        img = ctx.image()
        if img is None:
            return {"status": "skipped", "reason": "cannot decode image"}
        faces = self._run_app(app, img, params)
        return {"status": "ok", "faces": faces, "detected": len(faces)}

    def _detect_video(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        app: Any,
        params: Any,
    ) -> dict[str, Any]:
        # Pull keyframe timestamps from basic.scene_detect output.
        frame_times: list[float] = []
        data = ctx.result_of("basic.scene_detect")
        if data:
            scenes = data.get("scenes", [])
            frame_times = [
                float(s["keyframe"]) for s in scenes if "keyframe" in s
            ]
        if not frame_times:
            frame_times = [0.0]

        frame_times = frame_times[: params.max_video_frames]
        frames = ctx.frames(at_seconds=frame_times)

        seen_vecs: list[list[float]] = []
        all_faces: list[dict[str, Any]] = []
        for img in frames:
            if img is None:
                continue
            new_faces = []
            for face in self._run_app(app, img, params):
                vec = face["embedding"]
                if seen_vecs and self._closest(seen_vecs, vec) >= params.video_dedup_threshold:
                    continue  # already attributed to a person in this video
                new_faces.append(face)
            seen_vecs.extend(f["embedding"] for f in new_faces)
            all_faces.extend(new_faces)

        return {
            "status": "ok",
            "faces": all_faces,
            "detected": len(all_faces),
            "frames_sampled": len(frame_times),
        }

    def _run_app(self, app: Any, img: Any, params: Any) -> list[dict[str, Any]]:
        faces = app.get(img, max_num=params.max_faces)
        out: list[dict[str, Any]] = []
        for f in faces:
            bbox = f.bbox
            out.append(
                {
                    "embedding": [round(float(x), 6) for x in f.embedding.tolist()],
                    "confidence": round(float(f.det_score), 4),
                    "box": [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])],
                }
            )
        return out

    @staticmethod
    def _closest(vecs: list[list[float]], vec: list[float]) -> float:
        from hometrove.faces import cosine_similarity

        best = 0.0
        for v in vecs:
            s = cosine_similarity(v, vec)
            if s > best:
                best = s
        return best
