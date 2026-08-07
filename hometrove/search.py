"""Semantic + keyword hybrid search (M1-7).

Two recall paths, fused with Reciprocal Rank Fusion (RRF):

* vector recall — the query is encoded to a 1024-dim vector (deterministic
  hash today; the real jina-clip-v2 text encoder replaces ``_encode_query``
  in place, matching ``embedding.jina_clip``'s image encoder) and searched
  against the ``embedding_vec`` index. Hits carry their embedding's
  ``scope`` + scene span so a video hit can jump to the exact second.
* keyword recall — SQL ``LIKE`` over the text-bearing plugin outputs
  (mock.tags / mock.category / exif / basic.info), for the exact-match path
  the vector store can't express.

Both paths return ``(asset_id, embedding_id, scope, t_start, t_end, rank)``
candidates; RRF merges them into one ranked list. The query text can also
carry a ``scope:`` prefix (e.g. ``scope:scene sunset``) to restrict recall.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.models import Asset, AsrTranscript, Embedding, PluginResult
from hometrove.plugins.api import MediaType
from hometrove.vector import VECTOR_DIM, get_index

# Keyword recall: plugin results whose ``result_json`` is searched with LIKE.
_KEYWORD_PLUGINS = ("mock.tags", "mock.category", "exif", "basic.info")

# RRF: 1 / (k + rank). ``k`` is the standard 60.
_RRF_K = 60.0


@dataclass
class SearchHit:
    """One fused hit."""

    asset_id: int
    embedding_id: int | None
    scope: str
    t_start: float | None
    t_end: float | None
    score: float
    rank: int


def _encode_query(query: str) -> list[float]:
    """Deterministic query vector (mock). Replaced by real text encoder later."""
    import numpy as np

    words = [w for w in query.lower().split() if w]
    vec = np.zeros(VECTOR_DIM, dtype=np.float64)
    for i, w in enumerate(words):
        h = hashlib.sha256(w.encode()).digest()
        seed = int.from_bytes(h[:8], "big")
        # Simple deterministic pseudo-random direction per word.
        rng = np.random.default_rng(seed)
        vec += rng.standard_normal(VECTOR_DIM)
    norm = float(np.linalg.norm(vec)) or 1.0
    return [round(float(x) / norm, 6) for x in vec]


def _strip_scope(q: str) -> tuple[str, Optional[str]]:
    """Parse an optional ``scope:scene|image|audio`` prefix from the query."""
    for prefix in ("scope:", "scope="):
        if q.strip().startswith(prefix):
            rest = q.strip()[len(prefix):]
            head, _, tail = rest.partition(" ")
            if head in ("scene", "image", "caption", "audio"):
                return tail.strip() or "", head
    return q.strip(), None


def _vector_recall(
    session: Session,
    vec: list[float],
    scope: Optional[str],
    k: int = 40,
) -> list[tuple[SearchHit, int]]:
    """Nearest neighbours -> SearchHit, rank (0-based). Returns [] if no index."""
    try:
        rows = get_index().search(vec, k=k)
    except Exception:  # noqa: BLE001 — sqlite-vec absent / empty index
        return []

    hits: list[tuple[SearchHit, int]] = []
    for rank, (emb_id, dist) in enumerate(rows):
        emb = session.get(Embedding, emb_id)
        if emb is None:
            continue
        if scope is not None and emb.scope != scope:
            continue
        hits.append(
            (
                SearchHit(
                    asset_id=emb.asset_id,
                    embedding_id=emb.id,
                    scope=emb.scope,
                    t_start=emb.t_start,
                    t_end=emb.t_end,
                    score=0.0,  # filled by RRF
                    rank=rank + 1,
                ),
                rank + 1,  # 1-based rank for RRF
            )
        )
    return hits


def _keyword_recall(
    session: Session,
    q: str,
    limit: int = 40,
    scope: Optional[str] = None,
) -> list[tuple[SearchHit, int]]:
    """LIKE matching over text-bearing plugin results -> SearchHit, rank.

    ``scope`` narrows the recall source: ``audio`` only matches transcript
    rows; any other / unset scope returns both plugin results and
    transcripts. This keeps ``scope:audio`` precise while leaving the
    default hybrid path untouched.
    """
    hits: list[tuple[SearchHit, int]] = []
    include_plugin_results = scope != "audio"
    if include_plugin_results:
        for plugin_id in _KEYWORD_PLUGINS:
            rows = session.execute(
                select(PluginResult.asset_id).where(
                    PluginResult.plugin_id == plugin_id,
                    PluginResult.status == "ok",
                    PluginResult.result_json.ilike(f"%{q}%"),
                )
                .limit(limit)
            ).scalars().all()
            for rank, asset_id in enumerate(rows):
                hits.append(
                    (
                        SearchHit(
                            asset_id=asset_id,
                            embedding_id=None,
                            scope="keyword",
                            t_start=None,
                            t_end=None,
                            score=0.0,
                            rank=rank + 1,
                        ),
                        rank + 1,
                    )
                )

    # ASR transcripts (M1-10): match the spoken text and expose ``t_start``
    # so a video hit can jump to the cue that matched the query.
    audio_rows = session.execute(
        select(AsrTranscript).where(AsrTranscript.text.ilike(f"%{q}%"))
    ).scalars().all()
    for rank, row in enumerate(audio_rows):
        hits.append(
            (
                SearchHit(
                    asset_id=row.asset_id,
                    embedding_id=None,
                    scope="audio",
                    t_start=row.t_start,
                    t_end=row.t_end,
                    score=0.0,
                    rank=rank + 1,
                ),
                rank + 1,
            )
        )
    return hits


def _rrf(candidates: list[list[tuple[SearchHit, int]]]) -> list[SearchHit]:
    """Reciprocal rank fusion across recall paths."""
    fused: dict[tuple[int, str, Optional[float]], SearchHit] = {}
    scores: dict[tuple[int, str, Optional[float]], float] = {}
    for path in candidates:
        for hit, rank in path:
            key = (hit.asset_id, hit.scope, hit.t_start)
            if key in fused:
                # Keep the earliest (best) rank seen for the asset.
                if rank < fused[key].rank:
                    fused[key].rank = rank
                scores[key] += 1.0 / (_RRF_K + rank)
            else:
                fused[key] = hit
                scores[key] = 1.0 / (_RRF_K + rank)
    out = []
    for i, (key, hit) in enumerate(fused.items()):
        hit.score = round(scores[key], 6)
        out.append(hit)
    out.sort(key=lambda h: h.score, reverse=True)
    for i, h in enumerate(out):
        h.rank = i + 1
    return out


def search(
    session: Session,
    query: str,
    limit: int = 40,
) -> dict[str, Any]:
    """Run hybrid search and return results in the API shape."""
    q, scope = _strip_scope(query)
    if scope not in (None, "image", "scene", "audio"):
        # Unknown scope: degrade to a plain keyword search rather than 400.
        scope = None
    if not q:
        return {"query": query, "total": 0, "items": []}

    candidates: list[list[tuple[SearchHit, int]]] = []
    candidates.append(_vector_recall(session, _encode_query(q), scope))
    if scope in (None, "image", "scene", "audio"):
        # Keyword recall is scope-agnostic (matches tags / exif text); skip it
        # when the caller pinned a scene/image scope so text fields don't leak
        # out-of-scope results. ``audio`` is included so transcript hits can
        # carry the matching ``t_start``.
        candidates.append(_keyword_recall(session, q, scope=scope))

    fused = _rrf(candidates)

    # Join asset metadata for the response.
    assets: dict[int, Asset] = {}
    if fused:
        ids = [h.asset_id for h in fused]
        assets = {
            a.id: a
            for a in session.execute(select(Asset).where(Asset.id.in_(ids))).scalars().all()
        }

    items = []
    for h in fused[:limit]:
        a = assets.get(h.asset_id)
        if a is None:
            continue
        # v1 trash: drop hits whose asset has been soft-deleted so the
        # search results never resurrect a trashed item.
        if a.deleted_at is not None:
            continue
        items.append(
            {
                "asset_id": a.id,
                "media_type": a.media_type,
                "duration_sec": a.duration_sec,
                "score": h.score,
                "rank": h.rank,
                "scope": h.scope,
                "t_start": h.t_start,
                "t_end": h.t_end,
                "can_seek": a.media_type == MediaType.VIDEO.value and h.t_start is not None,
            }
        )
    return {
        "query": query,
        "total": len(fused),
        "items": items,
    }
