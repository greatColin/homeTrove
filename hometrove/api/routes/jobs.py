from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from hometrove.db import get_db, session_scope
from hometrove.events import BUS, JobEvent
from hometrove.models import Asset, Job


router = APIRouter(prefix="/api", tags=["jobs"])


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

    Uses the configured media roots. Designed to be cheap to call repeatedly;
    the scanner deduplicates by absolute path.
    """
    # Local imports to avoid pulling config/settings at import-time.
    from hometrove.config import get_settings
    from hometrove.scanner import discover, enqueue_pending, upsert_assets

    settings = get_settings()
    roots = settings.media_roots_paths
    if not roots:
        return {"new": 0, "skipped": 0, "enqueued": 0, "note": "no media roots configured"}
    discovered = list(discover(roots))
    new, skipped = upsert_assets(session, discovered)
    enqueued = enqueue_pending(session)
    return {"new": new, "skipped": skipped, "enqueued": enqueued}


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
