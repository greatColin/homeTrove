"""``embedding.jina_clip`` — semantic image / scene embeddings (M1-6).

M0's ``mock.faces`` proved the pattern: ship a deterministic, model-free
plugin whose output shape matches the real model's, so the frontend, the
storage, and the search pipeline can be built and tested before the heavy
model lands. This plugin follows the same route:

* currently emits **deterministic pseudo-vectors** (seeded from the asset id
  + content hash, then normalised to unit length in 1024 dims);
* the real jina-clip-v2 encoder replaces ``_encode_image`` / ``_encode_frame``
  in place later — output shape and storage stay identical.

Output is stored twice: the ``Embedding`` row (JSON vector + scope/timing
metadata, source of truth) and a copy in the ``embedding_vec`` sqlite-vec
index for nearest-neighbour search. ``run()`` is idempotent: vectors from a
previous run of this plugin for the same asset are dropped first.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Optional

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext, resolve_asset_path
from hometrove.plugins.base import BasePlugin
from hometrove.vector import VECTOR_DIM


def _seed(asset: AssetLike) -> int:
    raw = f"{asset.id}:{asset.content_hash_prefix or asset.path}"
    return int(hashlib.sha256(raw.encode()).hexdigest(), 16)


def _norm(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 6) for x in vec]


def _hash_vec(*parts: str, dim: int = VECTOR_DIM) -> list[float]:
    """Deterministic unit vector from arbitrary string parts."""
    out: list[float] = []
    for d in range(dim):
        h = hashlib.sha256("|".join((*parts, f"d{d}")).encode()).digest()
        out.append(int.from_bytes(h[:8], "big") / 2**64 * 2.0 - 1.0)
    return _norm(out)


def _encode_image(asset: AssetLike, img: Any) -> list[float]:
    """Pseudo-vector for a whole image.

    Mixes the asset identity with a coarse colour-histogram signature so two
    visually different photos of the same asset id-space still differ, while
    the same file stays stable across re-runs.
    """
    signature: list[str] = []
    if img is not None and getattr(img, "shape", None):
        try:
            flat = img.reshape(-1)
            bucket = max(1, flat.size // 256)
            sig = flat[::bucket][:256].astype("int64").sum()
            signature.append(f"c{sig}")
        except Exception:  # noqa: BLE001
            signature.append("c0")
    return _hash_vec("clip", str(asset.id), asset.content_hash_prefix or "", *signature)


def _encode_frame(asset: AssetLike, ts: float, img: Any) -> list[float]:
    """Pseudo-vector for one video scene keyframe (includes the timestamp)."""
    signature: list[str] = []
    if img is not None and getattr(img, "shape", None):
        try:
            flat = img.reshape(-1)
            bucket = max(1, flat.size // 128)
            signature.append(f"c{flat[::bucket][:128].astype('int64').sum()}")
        except Exception:  # noqa: BLE001
            signature.append("c0")
    return _hash_vec("clip", str(asset.id), asset.content_hash_prefix or "", f"t{ts:.3f}", *signature)


class EmbeddingJinaClipPlugin(BasePlugin):
    id: str = "embedding.jina_clip"
    name: str = "语义向量（图像/场景）"
    description: str = "计算图片/视频场景的语义向量（1024 维），用于以图搜图和语义搜索向量召回"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    # Videos need scene boundaries before embedding so each scene gets its own
    # vector. Unlike face.detect (where keyframes are only a dedup nicety),
    # scene scope is this plugin's core output — so scene_detect is a hard DAG
    # dependency. It is safe for image assets: scene_detect's job for an image
    # resolves to ``skipped``, which the dependency check treats as satisfied.
    depends_on: list[str] = ["basic.info", "basic.scene_detect"]

    class ParamsModel(BaseModel):
        dim: int = VECTOR_DIM
        max_scenes: int = 24   # cap scene vectors per video

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.05, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: EmbeddingJinaClipPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        if ctx.db is None:
            return {"status": "skipped", "reason": "no database context"}

        src = resolve_asset_path(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        try:
            # Idempotent re-run: drop this plugin's stale vectors first.
            from hometrove.vector import delete_embeddings

            delete_embeddings(ctx.db, asset.id, plugin_id=self.id)
            if asset.media_type == MediaType.VIDEO.value:
                return self._embed_video(asset, ctx, params)
            return self._embed_image(asset, ctx, params)
        except Exception as exc:  # noqa: BLE001  — decode/embed failure -> skip
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

    def _embed_image(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        params: Any,
    ) -> dict[str, Any]:
        img = ctx.image()
        vec = _encode_image(asset, img)
        self._store(asset, ctx, "image", vec, None, None)
        return {"status": "ok", "scope": "image", "dim": len(vec), "vectors": 1}

    def _embed_video(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        params: Any,
    ) -> dict[str, Any]:
        # Scene keyframes from basic.scene_detect, opportunistically.
        scenes: list[dict[str, Any]] = []
        data = ctx.result_of("basic.scene_detect")
        if data:
            scenes = data.get("scenes", [])
        scenes = scenes[: params.max_scenes]

        vectors = 0
        if scenes:
            times = [
                float(s.get("keyframe", (s.get("start", 0.0) + s.get("end", 0.0)) / 2))
                for s in scenes
            ]
            frames = ctx.frames(at_seconds=times)
            for scene, frame in zip(scenes, frames):
                ts = float(scene.get("keyframe", 0.0))
                vec = _encode_frame(asset, ts, frame)
                self._store(
                    asset, ctx, "scene", vec,
                    float(scene.get("start") or 0.0),
                    float(scene.get("end") or 0.0),
                )
                vectors += 1

        # Fall back to a single cover-frame image vector when there are no
        # scenes (e.g. static footage) so every video still gets an entry.
        if vectors == 0:
            frame = ctx.frames(at_seconds=[0.0])
            vec = _encode_frame(asset, 0.0, frame[0] if frame else None)
            self._store(asset, ctx, "image", vec, None, None)
            vectors = 1

        return {
            "status": "ok",
            "scope": "image" if vectors == 1 and not scenes else "scene",
            "dim": VECTOR_DIM,
            "vectors": vectors,
        }

    def _store(
        self,
        asset: AssetLike,
        ctx: PluginContext,
        scope: str,
        vec: list[float],
        t_start: Optional[float],
        t_end: Optional[float],
    ) -> None:
        from hometrove.models import Embedding
        from hometrove.vector import get_index

        assert ctx.db is not None
        session = ctx.db
        row = Embedding(
            asset_id=asset.id,
            plugin_id=self.id,
            plugin_version=self.version,
            scope=scope,
            t_start=t_start,
            t_end=t_end,
            embedding_json=json.dumps(vec),
        )
        session.add(row)
        session.flush()  # obtain row.id for the index
        get_index().upsert(row.id, vec, session=session)


__all__ = ["EmbeddingJinaClipPlugin"]
