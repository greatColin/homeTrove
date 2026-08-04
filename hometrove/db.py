from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import StaticPool, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from hometrove.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().resolved_database_url()
        connect_args = {"check_same_thread": False}
        # StaticPool keeps a single shared connection — required for in-memory sqlite tests.
        if url.endswith(":memory:") or url.endswith("/:memory:"):
            _engine = create_engine(
                url,
                connect_args=connect_args,
                poolclass=StaticPool,
                future=True,
            )
        else:
            _engine = create_engine(
                url,
                connect_args=connect_args,
                future=True,
            )

        @event.listens_for(_engine, "connect")
        def _set_pragma(dbapi_connection, _):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)

        # Auto-create the schema on first use. This is intentionally
        # unconditional so the API server, the worker, and CLI scripts all
        # share the same path. Alembic migration for production remains the
        # authoritative upgrade procedure (see ``hometrove migrate``).
        from hometrove.db import Base  # noqa: F401  ensure models registered via models.py
        from hometrove import models  # noqa: F401
        Base.metadata.create_all(_engine)
    return _engine


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
