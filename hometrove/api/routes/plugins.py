from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import Asset, Job, PluginConfig
from hometrove.plugins.registry import REGISTRY
from hometrove.scanner import enqueue_pending

router = APIRouter(prefix="/api", tags=["plugins"])


def _plugin_dto(p, row: PluginConfig | None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if row is not None and row.params_json:
        try:
            params = json.loads(row.params_json)
        except json.JSONDecodeError:
            params = {}
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "version": p.version,
        "supported_media": sorted(p.supported_media),
        "depends_on": list(p.depends_on),
        "enabled": bool(row.enabled) if row is not None else True,
        "params": params,
        "params_schema": p.ParamsModel.model_json_schema(),
    }


@router.get("/plugins")
def list_plugins(session: Session = Depends(get_db)):
    rows = {
        r.plugin_id: r
        for r in session.query(PluginConfig).all()
    }
    plugins = []
    for p in REGISTRY.list():
        plugins.append(_plugin_dto(p, rows.get(p.id)))
    # Deterministic order: registry order, not dict hashing.
    plugins.sort(key=lambda x: x["id"])
    return {"items": plugins}


class PluginUpdate(BaseModel):
    enabled: bool
    params: dict[str, Any] | None = None


@router.put("/plugins/{plugin_id}")
def update_plugin(plugin_id: str, body: PluginUpdate, session: Session = Depends(get_db)):
    plugin = REGISTRY.get(plugin_id) if plugin_id in {
        p.id for p in REGISTRY.list()
    } else None
    if plugin is None:
        raise HTTPException(404, "unknown plugin")

    row = session.get(PluginConfig, plugin_id)
    if row is None:
        row = PluginConfig(plugin_id=plugin_id, enabled=1 if body.enabled else 0)
        session.add(row)
    else:
        row.enabled = 1 if body.enabled else 0

    if body.params is not None:
        # Validate against the plugin's ParamsModel before persisting so a bad
        # payload surfaces as a 422, not at worker time.
        try:
            plugin.ParamsModel.model_validate(body.params)
        except Exception as exc:  # noqa: BLE001  — surface pydantic's detail
            raise HTTPException(422, f"invalid params: {exc}")
        row.params_json = json.dumps(body.params, ensure_ascii=False)

    session.commit()

    # Disabling a plugin should release its in-memory resources (e.g. loaded
    # models). On-disk artifacts are deliberately untouched.
    if not body.enabled:
        try:
            plugin.shutdown()
        except Exception:  # noqa: BLE001  — shutdown must not break the API call
            pass

    fresh = session.get(PluginConfig, plugin_id)
    return _plugin_dto(plugin, fresh)


@router.post("/plugins/{plugin_id}/rerun")
def rerun_plugin(plugin_id: str, session: Session = Depends(get_db)):
    """Force-requeue every asset for one plugin.

    Completed / failed jobs for the plugin are dropped and the plugin is
    re-enqueued for all compatible assets, so changing its params and hitting
    rerun recomputes the whole library (e.g. after tuning scene-detection
    sensitivity). Pending / running jobs are left untouched to avoid
    duplicating in-flight work.
    """
    plugin = REGISTRY.get(plugin_id) if plugin_id in {
        p.id for p in REGISTRY.list()
    } else None
    if plugin is None:
        raise HTTPException(404, "unknown plugin")

    row = session.get(PluginConfig, plugin_id)
    if row is not None and not row.enabled:
        raise HTTPException(400, "plugin is disabled — enable it before rerunning")

    dropped = session.execute(
        delete(Job).where(
            Job.plugin_id == plugin_id,
            Job.state.in_(["done", "failed"]),
        )
    ).rowcount
    enqueued = enqueue_pending(session, plugin_ids=[plugin_id])
    return {"ok": True, "dropped": dropped, "enqueued": enqueued}


class RerunCandidatesResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int


@router.get("/plugins/{plugin_id}/rerun-candidates", response_model=RerunCandidatesResponse)
def list_rerun_candidates(
    plugin_id: str,
    q: str | None = Query(None, description="Filename or path substring"),
    media_type: str | None = Query(None, description="Filter by media type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
):
    """List assets that can be rerun for a plugin.

    Results are filtered by the plugin's supported media types unless an
    explicit ``media_type`` is provided.
    """
    plugin = REGISTRY.get(plugin_id) if plugin_id in {
        p.id for p in REGISTRY.list()
    } else None
    if plugin is None:
        raise HTTPException(404, "unknown plugin")

    media_types = [media_type] if media_type else sorted(plugin.supported_media)
    if not media_types:
        return {"items": [], "total": 0}

    conds = [Asset.media_type.in_(media_types), Asset.deleted_at.is_(None)]
    if q:
        pattern = f"%{q}%"
        conds.append(or_(Asset.filename.ilike(pattern), Asset.path.ilike(pattern)))

    total = session.scalar(select(func.count(Asset.id)).where(*conds)) or 0
    rows = session.execute(
        select(Asset.id, Asset.filename, Asset.path, Asset.media_type)
        .where(*conds)
        .order_by(Asset.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = [
        {
            "asset_id": r.id,
            "filename": r.filename,
            "path": r.path,
            "media_type": r.media_type,
        }
        for r in rows
    ]
    return {"items": items, "total": total}


class RerunSelectedRequest(BaseModel):
    asset_ids: list[int]


@router.post("/plugins/{plugin_id}/rerun-selected")
def rerun_selected(
    plugin_id: str,
    body: RerunSelectedRequest,
    session: Session = Depends(get_db),
):
    """Drop done/failed jobs for the given plugin and re-enqueue selected assets."""
    plugin = REGISTRY.get(plugin_id) if plugin_id in {
        p.id for p in REGISTRY.list()
    } else None
    if plugin is None:
        raise HTTPException(404, "unknown plugin")

    row = session.get(PluginConfig, plugin_id)
    if row is not None and not row.enabled:
        raise HTTPException(400, "plugin is disabled — enable it before rerunning")

    if not body.asset_ids:
        return {"ok": True, "dropped": 0, "enqueued": 0}

    # Drop only done/failed jobs for the selected assets so pending/running
    # work is not duplicated.
    dropped = session.execute(
        delete(Job).where(
            Job.plugin_id == plugin_id,
            Job.asset_id.in_(body.asset_ids),
            Job.state.in_(["done", "failed"]),
        )
    ).rowcount

    enqueued = enqueue_pending(
        session,
        plugin_ids=[plugin_id],
        asset_ids=body.asset_ids,
        media_types=sorted(plugin.supported_media) or None,
    )
    return {"ok": True, "dropped": dropped, "enqueued": enqueued}
