"""Vector search index.

M1-6 introduces semantic embedding so the search feature (M1-7) can do vector
recall. The backend is ``sqlite-vec`` (``vec0`` virtual table) — an embedded
ANN index with zero deployment cost, matching the README's "VectorIndex
protocol" isolation so a heavier backend (e.g. LanceDB) can replace it later.

Layout:

* ``embeddings`` (normal table, SQLAlchemy model) — the source of truth for
  each vector's metadata: ``asset_id``, ``scope`` (image/scene/caption),
  ``t_start``/``t_end`` (scene spans) and the raw vector as JSON.
* ``embedding_vec`` (``vec0`` virtual table) — a copy of the vectors used for
  nearest-neighbour search; ``rowid`` mirrors ``embeddings.id``.

Vector dimension is fixed to 1024 (jina-clip-v2 / bge-m3 output size) so the
``vec0`` table can be created once; see ``VECTOR_DIM``.
"""

from __future__ import annotations

import json
from typing import Optional, Protocol, runtime_checkable

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

VECTOR_DIM = 1024


@runtime_checkable
class VectorIndex(Protocol):
    """Contract for the nearest-neighbour store (see README §6.4).

    Implementations receive raw DBAPI connections so the same backend can be
    swapped (sqlite-vec today, LanceDB later) without touching callers.
    """

    def upsert(self, embedding_id: int, vec: list[float], session: Optional[Session] = None) -> None: ...

    def remove(self, embedding_id: int, session: Optional[Session] = None) -> None: ...

    def search(self, vec: list[float], k: int = 20) -> list[tuple[int, float]]: ...


class SQLiteVecIndex:
    """``sqlite-vec`` backed index using the shared engine's connection.

    The ``vec0`` virtual table is created eagerly by ``db.engine()``. Because
    ``vec0`` rows reference ``embeddings.id`` as ``rowid``, writes go through
    the same transaction as the ``Embedding`` insert — pass the session so
    SQLite's single-writer rule does not trip a ``database is locked`` error.
    """

    def upsert(self, embedding_id: int, vec: list[float], session: Optional[Session] = None) -> None:
        conn = _connection(session)
        conn.execute(
            text(
                "INSERT OR REPLACE INTO embedding_vec(rowid, embedding) "
                "VALUES (:rid, :vec)"
            ),
            {"rid": embedding_id, "vec": json.dumps(vec)},
        )
        _commit(conn, session)

    def remove(self, embedding_id: int, session: Optional[Session] = None) -> None:
        conn = _connection(session)
        conn.execute(
            text("DELETE FROM embedding_vec WHERE rowid = :rid"),
            {"rid": embedding_id},
        )
        _commit(conn, session)

    def search(self, vec: list[float], k: int = 20) -> list[tuple[int, float]]:
        from hometrove.db import engine

        eng = engine()
        with eng.connect() as c:
            rows = c.execute(
                text(
                    "SELECT rowid, distance FROM embedding_vec "
                    "WHERE embedding match :vec AND k = :k"
                ),
                {"vec": json.dumps(vec), "k": k},
            ).fetchall()
        return [(int(r[0]), float(r[1])) for r in rows]


def _connection(session: Optional[Session]):
    """Reuse the session's bound connection (single write txn) or open a new one."""
    if session is not None:
        return session.connection()
    from hometrove.db import engine

    eng = engine()
    return eng.connect()


def _commit(conn, session: Optional[Session]) -> None:
    if session is None:
        conn.commit()
        conn.close()


def _default_index() -> VectorIndex:
    return SQLiteVecIndex()


_INDEX: Optional[VectorIndex] = None


def get_index() -> VectorIndex:
    """Process-wide singleton index (mirrors the ``db.engine()`` pattern)."""
    global _INDEX
    if _INDEX is None:
        _INDEX = _default_index()
    return _INDEX


def reset_index() -> None:
    global _INDEX
    _INDEX = None


def delete_embeddings(session: Session, asset_id: int, plugin_id: str | None = None) -> int:
    """Remove an asset's embedding rows (and their index copies).

    Used by plugins to stay idempotent on re-run: stale vectors from a
    previous version of the same plugin are dropped before inserting fresh
    ones. Returns the number of rows removed.
    """
    from hometrove.models import Embedding

    stmt = select(Embedding.id).where(Embedding.asset_id == asset_id)
    if plugin_id:
        stmt = stmt.where(Embedding.plugin_id == plugin_id)
    ids = [r[0] for r in session.execute(stmt).all()]

    idx = get_index()
    for _id in ids:
        idx.remove(_id, session=session)

    dstmt = delete(Embedding).where(Embedding.id.in_(ids)) if ids else delete(Embedding).where(False)
    result = session.execute(dstmt)
    return int(result.rowcount or 0)
