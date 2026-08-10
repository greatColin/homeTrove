"""``embedding.bge_m3`` — semantic text embeddings for VLM captions (M1-6).

Companion to ``embedding.jina_clip`` (image / scene vectors). This plugin reads
``vlm.qwen3vl``'s latest captions for an asset and encodes each description
text into a 1024-dim unit vector stored with ``scope="caption"``, carrying the
scene timestamp so a caption hit can jump the player to the exact second.

Like its image twin, it currently emits **deterministic pseudo-vectors**
(seeded from the asset id + caption text + timestamp, normalised to unit
length). The real bge-m3 text encoder replaces ``_encode_text`` in place later
— output shape and storage stay identical. The mock stage keeps the caption
search path exercisable on a vanilla install with no model download.

Output is stored twice: the ``Embedding`` row (JSON vector + scope/timing
metadata) and a copy in the ``embedding_vec`` sqlite-vec index.
``run()`` is idempotent: vectors from a previous run for the same asset are
dropped first.
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


def _encode_text(asset: AssetLike, caption: str, t: Optional[float]) -> list[float]:
    """Pseudo-vector for one caption text.

    Seed with the asset identity + the caption text + the timestamp so two
    different captions of the same asset differ, while the same caption stays
    stable across re-runs. Replaced by the real bge-m3 encoder in place.
    """
    ts = f"t{t:.3f}" if t is not None else "t-"
    return _hash_vec("bge3", str(asset.id), asset.content_hash_prefix or "", ts, caption)


class EmbeddingBgeM3Plugin(BasePlugin):
    id: str = "embedding.bge_m3"
    name: str = "语义向量（描述文本，bge-m3 占位）"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value, MediaType.VIDEO.value}
    # Captions come from vlm.qwen3vl; it must run (or skip) first.
    depends_on: list[str] = ["basic.info", "vlm.qwen3vl"]

    class ParamsModel(BaseModel):
        dim: int = VECTOR_DIM
        max_captions: int = 24   # cap caption vectors per asset

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.05, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        params: EmbeddingBgeM3Plugin.ParamsModel = ctx.params  # type: ignore[assignment]
        if ctx.db is None:
            return {"status": "skipped", "reason": "no database context"}

        src = resolve_asset_path(asset)
        if src is None:
            return {"status": "skipped", "reason": "source file missing"}

        data = ctx.result_of("vlm.qwen3vl")
        if not data:
            return {"status": "skipped", "reason": "no vlm.qwen3vl result"}

        captions = (data.get("captions") or [])[: params.max_captions]
        if not captions:
            return {"status": "ok", "scope": "caption", "dim": VECTOR_DIM, "vectors": 0}

        try:
            # Idempotent re-run: drop this plugin's stale caption vectors first.
            from hometrove.vector import delete_embeddings

            delete_embeddings(ctx.db, asset.id, plugin_id=self.id)
            vectors = 0
            for cap in captions:
                text = str(cap.get("caption") or "").strip()
                if not text:
                    continue
                t = cap.get("t")
                t_f = float(t) if t is not None else None
                vec = _encode_text(asset, text, t_f)
                self._store(asset, ctx, vec, t_f, t_f)
                vectors += 1
            return {"status": "ok", "scope": "caption", "dim": VECTOR_DIM, "vectors": vectors}
        except Exception as exc:  # noqa: BLE001  — encode/store failure -> skip
            return {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"}

    def _store(
        self,
        asset: AssetLike,
        ctx: PluginContext,
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
            scope="caption",
            t_start=t_start,
            t_end=t_end,
            embedding_json=json.dumps(vec),
        )
        session.add(row)
        session.flush()  # obtain row.id for the index
        get_index().upsert(row.id, vec, session=session)


__all__ = ["EmbeddingBgeM3Plugin"]
