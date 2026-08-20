from __future__ import annotations

import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, exists, func, not_, or_, select
from sqlalchemy.orm import Session

from hometrove.db import get_db, session_scope
from hometrove.events import BUS, JobEvent
from hometrove.models import Asset, Job, PluginConfig
from hometrove.plugins.api import MediaType
from hometrove.plugins.registry import REGISTRY


router = APIRouter(prefix="/api", tags=["jobs"])

_JOB_STATES = ("pending", "running", "done", "failed")
_ACTIVE_STATES = ("pending", "running")


def _stats(session: Session) -> dict:
    total = session.execute(select(func.count(Job.id))).scalar_one()
    by_state = dict(
        session.execute(
            select(Job.state, func.count(Job.id)).group_by(Job.state)
        ).all()
    )
    pending = by_state.get("pending", 0)
    running = by_state.get("running", 0)
    done = by_state.get("done", 0)
    failed = by_state.get("failed", 0)
    total_est = session.execute(select(func.coalesce(func.sum(Job.est_cost), 0.0))).scalar_one()
    done_est = session.execute(
        select(func.coalesce(func.sum(Job.est_cost), 0.0)).where(Job.state == "done")
    ).scalar_one()
    progress = (done_est / total_est) if total_est else (1.0 if total == done and total > 0 else 0.0)
    return {
        "total": total,
        "pending": pending,
        "running": running,
        "done": done,
        "failed": failed,
        "progress": float(progress),
        "total_est": float(total_est),
        "done_est": float(done_est),
    }


def _display_name(a: "Asset") -> str:
    if a is None:
        return "?"
    if "\0" in a.path:
        _, _, rest = a.path.partition("\0")
        return Path(rest).name
    return Path(a.path).name


def _aggregate_state(jobs: list) -> str:
    if any(j.state in ("pending", "running") for j in jobs):
        return "active"
    if any(j.state == "failed" for j in jobs):
        return "failed"
    return "done"


def _job_dto(j: Job) -> dict:
    return {
        "id": j.id,
        "plugin_id": j.plugin_id,
        "state": j.state,
        "attempts": j.attempts,
        "error": j.error,
        "est_cost": j.est_cost,
        "actual_cost": j.actual_cost,
        "enqueued_at": j.enqueued_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
    }


@router.get("/jobs")
def list_jobs(limit: int = 100, session: Session = Depends(get_db)):
    # Group by file (asset), newest enqueued job first. A single file may have
    # several plugin jobs (e.g. basic.info) — surface one row per file.
    recent = (
        session.execute(select(Job).order_by(desc(Job.enqueued_at)).limit(2000))
        .scalars()
        .all()
    )
    by_asset: dict[int, list[Job]] = {}
    for j in recent:
        by_asset.setdefault(j.asset_id, []).append(j)

    items: list[dict] = []
    for asset_id, jobs in by_asset.items():
        a = session.get(Asset, asset_id)
        latest = jobs[0]
        items.append(
            {
                "asset_id": asset_id,
                "filename": _display_name(a),
                "media_type": a.media_type if a is not None else None,
                "state": _aggregate_state(jobs),
                "enqueued_at": latest.enqueued_at,
                "jobs": [_job_dto(j) for j in jobs],
            }
        )
    items.sort(key=lambda x: x["enqueued_at"] or 0, reverse=True)
    return {"stats": _stats(session), "items": items[:limit]}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, session: Session = Depends(get_db)):
    j = session.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    if j.state == "running":
        raise HTTPException(409, "job is currently running")
    j.state = "pending"
    j.error = None
    j.started_at = None
    j.finished_at = None
    j.actual_cost = None
    session.add(j)
    session.commit()
    return {"ok": True}


@router.post("/scan")
def trigger_scan(session: Session = Depends(get_db)):
    """Run the scanner once.

    Only image and video assets are ingested; the worker no longer
    auto-enqueues plugins. Operators queue the queue by visiting the
    ``/api/plugins/queue-stats`` view and selecting assets per plugin.
    Designed to be cheap to call repeatedly; the scanner deduplicates by
    absolute path.
    """
    # Local imports to avoid pulling config/settings at import-time.
    from hometrove.config import get_settings
    from hometrove.scanner import discover, upsert_assets

    settings = get_settings()
    roots = settings.media_roots_paths
    if not roots:
        return {"new": 0, "skipped": 0, "note": "no media roots configured"}
    discovered = list(discover(roots))
    new, skipped = upsert_assets(session, discovered)
    return {"new": new, "skipped": skipped}


@router.get("/jobs/stream")
async def stream_events(request: Request):
    """SSE feed of job lifecycle events."""
    q = await BUS.subscribe()
    try:
        async def gen():
            try:
                # Initial snapshot.
                with session_scope() as s:
                    snapshot = {"stats": _stats(s), "ts": time.time()}
                yield JobEvent("snapshot", time.time(), snapshot).to_sse()
                async for event in BUS.stream(q):
                    if await request.is_disconnected():
                        break
                    yield event
            finally:
                await BUS.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream")
    except Exception:
        await BUS.unsubscribe(q)
        raise


# ---------------------------------------------------------------------------
# v2 queue management: per-plugin stats + paginated asset selection.
#
# The scan route above no longer auto-enqueues plugins. Operators visit
# ``GET /api/plugins/queue-stats?media_type=image|video`` to see, for each
# enabled plugin that supports that media type, how many assets are
# ``todo`` (never processed), ``active``, ``failed`` and ``done``. Clicking
# into a plugin opens a paginated list of assets filtered by state, and
# ``POST /api/assets/enqueue`` converts the selected asset_ids into Job
# rows. ``Asset`` rows are created only by ``/api/scan``.
# ---------------------------------------------------------------------------


def _plugin_state_counts(
    session: Session, plugin_id: str, media_type: str
) -> dict[str, int]:
    """Return counts of assets in each Job state for ``plugin_id`` restricted
    to ``media_type``.

    ``todo`` is derived as "asset of this media type with no Job for this
    plugin" (i.e. never processed, not even pending). The other three
    states are aggregated from the Job table directly.
    """
    total_assets = session.execute(
        select(func.count(Asset.id)).where(
            Asset.media_type == media_type,
            Asset.deleted_at.is_(None),
        )
    ).scalar_one()

    # Aggregate Job counts grouped by state. SQL UNIONs the four states so
    # we always return all four keys (0 for absent ones).
    rows = dict(
        session.execute(
            select(Job.state, func.count(Job.id))
            .join(Asset, Job.asset_id == Asset.id)
            .where(
                Job.plugin_id == plugin_id,
                Asset.media_type == media_type,
                Asset.deleted_at.is_(None),
            )
            .group_by(Job.state)
        ).all()
    )
    todo = total_assets - sum(rows.get(s, 0) for s in _JOB_STATES)
    if todo < 0:
        # Defensive: if older rows reference deleted assets the math can
        # dip below zero; clamp.
        todo = 0
    return {
        "todo": int(todo),
        "active": int(rows.get("pending", 0)) + int(rows.get("running", 0)),
        "failed": int(rows.get("failed", 0)),
        "done": int(rows.get("done", 0)),
    }


@router.get("/queue/stats")
def queue_stats(
    media_type: Literal["image", "video"] = Query(...),
    session: Session = Depends(get_db),
):
    """Return one row per enabled, non-stub plugin that supports the given
    media type.

    The list is the basis of the queue management tab on the client:
    operators click into a plugin to see its todo / active / failed lists.
    """
    disabled_ids = set(
        session.scalars(
            select(PluginConfig.plugin_id).where(PluginConfig.enabled == 0)
        ).all()
    )
    plugins = []
    for p in REGISTRY.list():
        if p.id in disabled_ids:
            continue
        if getattr(p, "category", None) == "stub":
            continue
        if media_type not in getattr(p, "supported_media", set()):
            continue
        plugins.append(p)

    items = []
    for p in plugins:
        try:
            name = p.name
        except Exception:  # noqa: BLE001
            name = p.id
        items.append(
            {
                "plugin_id": p.id,
                "name": name,
                **_plugin_state_counts(session, p.id, media_type),
            }
        )
    items.sort(key=lambda x: x["plugin_id"])
    return {"media_type": media_type, "plugins": items}


def _assets_for_plugin_state(
    session: Session,
    plugin_id: str,
    media_type: str,
    state: str,
    page: int,
    size: int,
) -> dict:
    """Paginated asset rows for one (plugin, media_type, state).

    ``state`` is one of:
      - ``todo``: no Job exists for the asset/plugin pair
      - ``active``: at least one Job exists in pending/running
      - ``failed``: most recent Job is failed (no pending/running)
      - ``done``: most recent Job is done (no pending/running)

    Sorted by ``Asset.created_at DESC`` so newly scanned files appear at
    the top.
    """
    if state not in ("todo", "active", "failed", "done"):
        raise HTTPException(400, f"invalid state: {state}")
    plugin = REGISTRY.get(plugin_id)
    if plugin is None:
        raise HTTPException(404, "unknown plugin")

    base = select(Asset).where(
        Asset.media_type == media_type,
        Asset.deleted_at.is_(None),
    )

    if state == "todo":
        # No Job at all for this (asset, plugin).
        no_job = ~exists().where(
            and_(Job.asset_id == Asset.id, Job.plugin_id == plugin_id)
        )
        stmt = base.where(no_job)
    elif state == "active":
        any_active = exists().where(
            and_(
                Job.asset_id == Asset.id,
                Job.plugin_id == plugin_id,
                Job.state.in_(_ACTIVE_STATES),
            )
        )
        stmt = base.where(any_active)
    else:
        # failed / done: most recent job has that state, no active jobs.
        most_recent_subq = (
            select(Job.state)
            .where(and_(Job.asset_id == Asset.id, Job.plugin_id == plugin_id))
            .order_by(desc(Job.enqueued_at))
            .limit(1)
            .scalar_subquery()
        )
        no_active = ~exists().where(
            and_(
                Job.asset_id == Asset.id,
                Job.plugin_id == plugin_id,
                Job.state.in_(_ACTIVE_STATES),
            )
        )
        stmt = base.where(
            and_(most_recent_subq == state, no_active)
        )

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    rows = (
        session.execute(
            stmt.order_by(desc(Asset.created_at), desc(Asset.id))
            .offset(max(0, (page - 1) * size))
            .limit(size)
        )
        .scalars()
        .all()
    )

    items = []
    for a in rows:
        items.append(
            {
                "id": a.id,
                "filename": _display_name(a),
                "media_type": a.media_type,
                "size_bytes": a.size_bytes,
                "mtime": a.mtime,
                "created_at": a.created_at,
                "width": a.width,
                "height": a.height,
                "duration_sec": a.duration_sec,
            }
        )

    return {
        "items": items,
        "page": page,
        "size": size,
        "total": total,
        "pages": (total + size - 1) // size if size else 1,
    }


@router.get("/queue/assets")
def list_assets_for_plugin(
    plugin_id: str = Query(...),
    media_type: Literal["image", "video"] = Query(...),
    state: Literal["todo", "active", "failed", "done"] = Query("todo"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_db),
):
    """Paginated asset list scoped to one plugin + state.

    The state semantics are documented in ``_assets_for_plugin_state``.
    """
    return _assets_for_plugin_state(
        session, plugin_id, media_type, state, page, size
    )


class EnqueueRequest(BaseModel):
    plugin_id: str
    asset_ids: list[int] = Field(default_factory=list)


@router.post("/queue/enqueue")
def enqueue_assets(body: EnqueueRequest, session: Session = Depends(get_db)):
    """Create a ``pending`` Job for each (asset, plugin) pair.

    Already-pending/running jobs are skipped (idempotent). ``done`` jobs
    are *not* skipped — re-enqueueing an asset forces a fresh run for
    that plugin. ``failed`` jobs become pending again, giving the user a
    manual retry path.
    """
    from hometrove.scanner import enqueue_pending

    plugin = REGISTRY.get(body.plugin_id)
    if plugin is None:
        raise HTTPException(404, "unknown plugin")
    if not body.asset_ids:
        return {"created": 0, "skipped": 0}

    # Reuse the scanner helper so the rules (estimate, live-check, etc.)
    # stay in one place. It accepts ``asset_ids`` and ``plugin_ids``.
    created = enqueue_pending(
        session,
        plugin_ids=[body.plugin_id],
        asset_ids=body.asset_ids,
    )
    return {"created": created, "skipped": len(body.asset_ids) - created}
