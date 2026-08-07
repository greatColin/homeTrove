"""v1 trash service: soft-delete + restore + permanent purge.

The trash is a *soft* delete: ``Asset.deleted_at`` is set to the current
epoch seconds when an asset is moved to trash. All default views
(``/api/assets``, search, facets, etc.) filter ``deleted_at IS NULL`` so
trashed assets disappear from the user-visible library immediately but
their rows, plugin results, and embeddings remain intact for restore.

Permanent purge (``purge_expired`` / ``empty_trash``) **never** touches
the on-disk file. M0 assumes media roots are read-only mounts, so the
purge is database-only — it cascades ``plugin_results``, ``embeddings``,
``face_embeddings``, ``asr_transcripts``, ``album_assets``, and ``jobs``
via the existing FK ``ON DELETE CASCADE`` constraints, then drops the
``assets`` row.

Retention defaults to ``Settings.trash_retention_days`` (30 days). The
worker can opt into auto-purge with ``Settings.trash_auto_purge``; the
CLI exposes a manual one-shot ``hometrove trash prune``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from hometrove.config import get_settings
from hometrove.models import Asset

log = logging.getLogger("hometrove.trash")


@dataclass(frozen=True)
class TrashAction:
    """Result of a trash / restore / purge call."""

    asset_id: int
    deleted_at: int | None  # None after restore or for already-live rows


def _now() -> int:
    return int(time.time())


def move_to_trash(session: Session, asset_id: int) -> TrashAction:
    """Soft-delete ``asset_id``. Idempotent: already-trashed rows keep their
    original ``deleted_at`` so retention math stays stable. Raises 404-ish
    ``LookupError`` (mapped to HTTP 404 by the route) when the row does
    not exist.
    """
    a = session.get(Asset, asset_id)
    if a is None:
        raise LookupError(f"asset {asset_id} not found")
    if a.deleted_at is None:
        a.deleted_at = _now()
        a.updated_at = a.deleted_at
        session.commit()
    return TrashAction(asset_id=a.id, deleted_at=a.deleted_at)


def restore_from_trash(session: Session, asset_id: int) -> TrashAction:
    """Undo ``move_to_trash``. Idempotent: live rows return ``deleted_at=None``
    unchanged. 404 on missing row.
    """
    a = session.get(Asset, asset_id)
    if a is None:
        raise LookupError(f"asset {asset_id} not found")
    if a.deleted_at is not None:
        a.deleted_at = None
        a.updated_at = _now()
        session.commit()
    return TrashAction(asset_id=a.id, deleted_at=a.deleted_at)


def list_trashed(session: Session, *, limit: int, offset: int = 0) -> tuple[list[Asset], int]:
    """Paginate trashed assets, newest deletion first.

    Returns ``(rows, total)``. ``total`` is the row count so the UI can
    show "N items in trash" without paginating to find out.
    """
    base = select(Asset).where(Asset.deleted_at.is_not(None))
    rows = (
        session.execute(
            base.order_by(Asset.deleted_at.desc(), Asset.id.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    total = session.execute(
        select(Asset.id).where(Asset.deleted_at.is_not(None))
    ).all()
    return list(rows), len(total)


def empty_trash(session: Session, *, older_than_seconds: int | None = None) -> int:
    """Permanently delete trashed rows from the database.

    ``older_than_seconds`` narrows the purge to rows whose ``deleted_at``
    is older than that many seconds ago — a None value empties the
    entire trash in one call (the "Empty trash now" button in the UI).

    Returns the number of rows dropped. Cascade FKs handle
    ``plugin_results`` / ``embeddings`` / ``face_embeddings`` /
    ``asr_transcripts`` / ``album_assets`` / ``jobs``.
    """
    stmt = delete(Asset).where(Asset.deleted_at.is_not(None))
    if older_than_seconds is not None:
        cutoff = _now() - older_than_seconds
        stmt = stmt.where(Asset.deleted_at < cutoff)
    result = session.execute(stmt)
    session.commit()
    return int(result.rowcount or 0)


def purge_expired(session: Session) -> int:
    """Drop trashed rows whose ``deleted_at`` is older than the configured
    retention. Returns the number of rows dropped.

    Pure convenience wrapper around :func:`empty_trash` so the worker
    tick and the CLI share one code path.
    """
    settings = get_settings()
    if settings.trash_retention_days <= 0:
        return 0
    older_than = settings.trash_retention_days * 86400
    dropped = empty_trash(session, older_than_seconds=older_than)
    if dropped:
        log.info(
            "trash purge: dropped %d asset(s) older than %d day(s)",
            dropped,
            settings.trash_retention_days,
        )
    return dropped
