"""``face.video`` — video face detection + recognition (InsightFace buffalo_l).

Reads scene-detected keyframes (preferred) or evenly-spaced frames,
detects faces on each, deduplicates the same person across frames by
cosine similarity so one shot is not counted repeatedly, and emits a
single 512-D embedding per unique identity seen in the video.

Faces are persisted by ``hometrove.face_cluster.cluster_faces_for_asset``
(worker invocation after ``run()``). This plugin only produces the
embeddings.

Shares the same InsightFace instance as ``face.image`` via
``hometrove.insightface_runtime``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from hometrove.insightface_runtime import ModelMissingError, acquire, release
from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin
from hometrove.faces import cosine_similarity

MODEL_NAME = "buffalo_l"
PLUGIN_ID = "face.video"


def _serialize_face(face: Any, *, frame_index: int | None, frame_t: float | None) -> dict[str, Any]:
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
        "frame_index": frame_index,
        "frame_t": frame_t,
    }


def _closest(vecs: list[list[float]], vec: list[float]) -> float:
    best = 0.0
    for v in vecs:
        s = cosine_similarity(v, vec)
        if s > best:
            best = s
    return best


class FaceVideoPlugin(BasePlugin):
    id: str = PLUGIN_ID
    name: str = "人脸识别（视频）"
    description: str = (
        "用 InsightFace buffalo_l 检测视频关键帧中的人脸，跨帧去重后输出"
        "每个人物一个 512 维向量。自动归组为「视频识别组」，供用户标记为"
        "人员。"
    )
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.VIDEO.value}
    # ``basic.scene_detect`` is the canonical keyframe source; we declare
    # it as a dependency so the worker waits for scene cuts before we run.
    depends_on: list[str] = ["basic.info", "basic.scene_detect"]
    category: str = "implemented"
    # Boot-loading the ~250MB buffalo_l pack takes a while; let the lifecycle
    # manager start this plugin on a background thread so the API stays up.
    heavy_startup: bool = True

    class ParamsModel(BaseModel):
        det_thresh: float = 0.5
        max_faces_per_frame: int = 0  # 0 = unlimited
        video_dedup_threshold: float = 0.45
        max_video_frames: int = 8

    def startup(self) -> None:
        acquire(MODEL_NAME)

    def shutdown(self) -> None:
        release()

    def estimate(self, asset: AssetLike) -> Cost:
        # Videos are heavier than images; scene_detect already did the work.
        return Cost(seconds=3.0, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: FaceVideoPlugin.ParamsModel = ctx.params  # type: ignore[assignment]

        # Pull keyframe timestamps from basic.scene_detect output. We treat
        # missing scene data as "fall back to a single midpoint frame" so a
        # scene-detect-disabled deployment still gets *something*.
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

        try:
            app = acquire(MODEL_NAME)
        except ModelMissingError as exc:
            return {
                "status": "error",
                "reason": f"缺少模型 {exc.model_name}",
                "model_name": exc.model_name,
                "model_dir": exc.model_dir,
            }

        # ``ctx.frames()`` is cheap (just scheduling keyframe extraction);
        # the heavy decode happens lazily on iteration.
        try:
            frames = ctx.frames(at_seconds=frame_times)
        except Exception as exc:  # noqa: BLE001
            release()
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

        seen_vecs: list[list[float]] = []
        all_faces: list[dict[str, Any]] = []
        for idx, (img, t) in enumerate(zip(frames, frame_times)):
            if img is None:
                continue
            try:
                raw = app.get(img, max_num=params.max_faces_per_frame or 0)
            except Exception as exc:  # noqa: BLE001
                # One bad frame shouldn't kill the whole video.
                all_faces.append(
                    {
                        "_frame_error": f"{type(exc).__name__}: {exc}",
                        "frame_index": idx,
                        "frame_t": float(t),
                    }
                )
                continue
            for f in raw:
                vec = [round(float(x), 6) for x in f.embedding.tolist()]
                if seen_vecs and _closest(seen_vecs, vec) >= params.video_dedup_threshold:
                    continue
                seen_vecs.append(vec)
                all_faces.append(_serialize_face(f, frame_index=idx, frame_t=float(t)))

        return {
            "status": "ok",
            "faces": all_faces,
            "detected": len(all_faces),
            "frames_sampled": len(frame_times),
            "source_plugin_id": PLUGIN_ID,
            "source_model_name": MODEL_NAME,
        }