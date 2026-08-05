from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import StaticPool, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from hometrove.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None
_ENGINE_LOCK = threading.Lock()


def _load_extensions(dbapi_connection, _) -> None:  # noqa: ANN001
    """Enable sqlite-vec (vector index) on every connection."""
    try:
        import sqlite_vec

        dbapi_connection.enable_load_extension(True)
        sqlite_vec.load(dbapi_connection)
        dbapi_connection.enable_load_extension(False)
    except ImportError:
        # sqlite-vec is optional at runtime: vector plugins degrade to a
        # plain-table fallback when the extension is not installed.
        pass


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        # Serialize schema bootstrap: ``serve`` starts the worker thread and
        # the API lifespan concurrently, and two connections racing to
        # ``CREATE TABLE assets`` trips SQLite's single-writer rule.
        with _ENGINE_LOCK:
            if _engine is None:
                _init_engine()
    return _engine


def _init_engine() -> None:
    global _engine, _SessionLocal
    url = get_settings().resolved_database_url()
    connect_args = {"check_same_thread": False}
    # StaticPool keeps a single shared connection — required for in-memory sqlite tests.
    if url.endswith(":memory:") or url.endswith("/:memory:"):
        e = create_engine(
            url,
            connect_args=connect_args,
            poolclass=StaticPool,
            future=True,
        )
    else:
        e = create_engine(
            url,
            connect_args=connect_args,
            future=True,
        )

    @event.listens_for(e, "connect")
    def _set_pragma(dbapi_connection, _):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(e, "connect", _load_extensions)

    _SessionLocal = sessionmaker(bind=e, autoflush=False, expire_on_commit=False)

    # Auto-create the schema on first use. This is intentionally
    # unconditional so the API server, the worker, and CLI scripts all
    # share the same path. Alembic migration for production remains the
    # authoritative upgrade procedure (see ``hometrove migrate``).
    from hometrove.db import Base  # noqa: F401  ensure models registered via models.py
    from hometrove import models  # noqa: F401
    Base.metadata.create_all(e)
    _create_vec_tables(e)

    # Publish the engine only after the schema exists. The worker thread and
    # the API lifespan bootstrap concurrently; publishing early would let the
    # other side observe a non-None engine while CREATE TABLE is still in
    # flight, and SELECT would then hit "no such table".
    _engine = e


def _create_vec_tables(_engine) -> None:  # noqa: ANN001
    """Create the sqlite-vec virtual table (vector search index).

    The virtual table holds ``(rowid, embedding)`` where ``rowid`` mirrors the
    ``embeddings`` table's primary key. The vector dimension must match the
    embedding models' output size; jina-clip-v2 / bge-m3 both emit 1024 dims.
    """
    from hometrove.vector import VECTOR_DIM

    try:
        from sqlalchemy import text

        with _engine.connect() as c:
            c.execute(
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS embedding_vec "
                    f"USING vec0(embedding float[{VECTOR_DIM}])"
                )
            )
            c.commit()
    except Exception:  # noqa: BLE001  — sqlite-vec unavailable / DDL failure
        pass


def session_factory() -> sessionmaker[Session]:
    engine()
    assert _SessionLocal is not None
    return _SessionLocal


def reset_engine() -> None:
    """Drop cached engine (used in tests)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    s = session_factory()()
    try:
        yield s
    finally:
        s.close()
