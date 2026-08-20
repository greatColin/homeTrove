"""``face.image`` — image face detection + recognition (InsightFace buffalo_l).

Detects every face in an image and emits a 512-D embedding per face. Faces
are persisted by ``hometrove.face_cluster.cluster_faces_for_asset`` (which
the worker invokes after ``run()`` returns) — ``face.image`` itself only
produces embeddings.

The shared InsightFace model is acquired via ``hometrove.insightface_runtime``
so that ``face.video`` and ``face.image`` do not each load their own
``FaceAnalysis`` instance. The refcount is incremented in ``startup()`` and
released in ``shutdown()``; a missing model pack surfaces as ``status=error``
with a "缺少模型" reason so the frontend can offer a download button.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from hometrove.insightface_runtime import ModelMissingError, acquire, release
from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin

MODEL_NAME = "buffalo_l"
PLUGIN_ID = "face.image"


def _serialize_face(face: Any) -> dict[str, Any]:
    bbox = face.bbox
    return {
        "embedding": [round(float(x), 6) for x in face.embedding.tolist()],
        "confidence": round(float(face.det_score), 4),
        "box": [
            int(bbox[0]),
            int(bbox[1]),
            int(bbox[2]),
            int(bbox[3]),
        ],
    }


class FaceImagePlugin(BasePlugin):
    id: str = PLUGIN_ID
    name: str = "人脸识别（图片）"
    description: str = (
        "用 InsightFace buffalo_l 检测图片中的人脸，输出 512 维向量。"
        "自动归组为「图片识别组」，供用户标记为人员。"
    )
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value}
    depends_on: list[str] = ["basic.info"]
    # ``requires_model`` is informational: the lifecycle manager reads
    # ``category`` for status gating, but we still propagate ModelMissingError
    # into status=error with a "缺少模型" reason so the frontend can offer
    # the download button.
    category: str = "implemented"

    class ParamsModel(BaseModel):
        det_thresh: float = 0.5
        max_faces: int = 0  # 0 = unlimited

    def startup(self) -> None:
        # Acquire shared FaceAnalysis; refcount++ so face.video stays alive.
        acquire(MODEL_NAME)

    def shutdown(self) -> None:
        release()

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.5, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: FaceImagePlugin.ParamsModel = ctx.params  # type: ignore[assignment]

        img = ctx.image()
        if img is None:
            return {"status": "skipped", "reason": "cannot decode image"}

        try:
            app = acquire(MODEL_NAME)
        except ModelMissingError as exc:
            return {
                "status": "error",
                "reason": f"缺少模型 {exc.model_name}",
                "model_name": exc.model_name,
                "model_dir": exc.model_dir,
            }

        try:
            try:
                faces = app.get(img, max_num=params.max_faces or 0)
            finally:
                # Per-run refcount: startup/shutdown cover long-term ownership,
                # but run() also takes a temporary ref so a concurrent
                # face.video shutdown cannot yank the singleton mid-detect.
                release()
        except Exception as exc:  # noqa: BLE001
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

        serialized = [_serialize_face(f) for f in faces]
        return {
            "status": "ok",
            "faces": serialized,
            "detected": len(serialized),
            "source_plugin_id": PLUGIN_ID,
            "source_model_name": MODEL_NAME,
        }