from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import Asset, PluginResult


router = APIRouter(prefix="/api", tags=["assets"])


# Which plugin result feeds which facet filter. Keyed by the facet name used
# in ``/api/assets?tags=..`` etc.; the value is the plugin id that produces it.
_FACET_PLUGIN = {
    "tags": "mock.tags",
    "category": "mock.category",
    "person": "mock.faces",
}


def _facet_asset_ids(session: Session, facet: str, value: str) -> list[int]:
    """Asset ids whose facet plugin result contains ``value``.

    Uses a JSON substring match on ``result_json`` — precise enough for M0's
    small libraries and works on any SQL backend. The facet plugins write
    values as JSON string keys.
    """
    plugin_id = _FACET_PLUGIN.get(facet)
    if plugin_id is None:
        raise HTTPException(400, f"unknown facet {facet!r}")
    rows = session.execute(
        select(PluginResult.asset_id).where(
            PluginResult.plugin_id == plugin_id,
            PluginResult.result_json.contains(f'"{value}"'),
        )
    ).scalars().all()
    return list(rows)


def _plugin_results(session: Session, asset_id: int) -> dict[str, dict]:
    """All plugin outputs for an asset, keyed by plugin id."""
    rows = (
        session.execute(
            select(PluginResult).where(PluginResult.asset_id == asset_id)
        )
        .scalars()
        .all()
    )
    out: dict[str, dict] = {}
    for pr in rows:
        try:
            data = json.loads(pr.result_json or "{}")
        except json.JSONDecodeError:
            data = {}
        out[pr.plugin_id] = {
            "status": pr.status,
            "version": pr.plugin_version,
            "elapsed_ms": pr.elapsed_ms,
            "finished_at": pr.finished_at,
            "data": data,
        }
    return out


def _to_asset_dto(a: Asset, *, basic: Optional[dict] = None) -> dict:
    return {
        "id": a.id,
        "path": a.path,
        "media_type": a.media_type,
        "size_bytes": a.size_bytes,
        "mtime": a.mtime,
        "taken_at": a.taken_at,
        "width": a.width,
        "height": a.height,
        "duration_sec": a.duration_sec,
        "updated_at": a.updated_at,
        "basic_info": basic,
    }


@router.get("/assets")
def list_assets(
    media_type: Optional[str] = Query(None, pattern="^(image|video|other)$"),
    cursor: Optional[int] = None,
    limit: int = Query(60, ge=1, le=500),
    tag: Optional[str] = None,
    category: Optional[str] = None,
    person: Optional[str] = None,
    session: Session = Depends(get_db),
):
    stmt = select(Asset)
    if media_type:
        stmt = stmt.where(Asset.media_type == media_type)

    # Facet filters narrow the result set to assets whose plugin output
    # contains the selected value.
    facet_ids: set[int] | None = None
    for facet, value in (("tags", tag), ("category", category), ("person", person)):
        if value:
            ids = set(_facet_asset_ids(session, facet, value))
            facet_ids = ids if facet_ids is None else (facet_ids & ids)
    if facet_ids is not None:
        stmt = stmt.where(Asset.id.in_(facet_ids))

    if cursor is not None:
        stmt = stmt.where(Asset.id < cursor)
    stmt = stmt.order_by(
        desc(Asset.taken_at).nulls_last(), desc(Asset.id)
    ).limit(limit)

    rows = session.execute(stmt).scalars().all()
    next_cursor = rows[-1].id if rows and len(rows) == limit else None

    basics: dict[int, dict] = {}
    if rows:
        prs = (
            session.execute(
                select(PluginResult).where(
                    PluginResult.asset_id.in_([r.id for r in rows]),
                    PluginResult.plugin_id == "basic.info",
                    PluginResult.status == "ok",
                )
            )
            .scalars()
            .all()
        )
        basics = {pr.asset_id: json.loads(pr.result_json) for pr in prs}

    return {
        "items": [_to_asset_dto(r, basic=basics.get(r.id)) for r in rows],
        "next_cursor": next_cursor,
    }


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, session: Session = Depends(get_db)):
    a = session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    pr = session.get(PluginResult, (a.id, "basic.info", "0.1.0"))
    basic = None
    if pr is not None:
        try:
            basic = json.loads(pr.result_json)
        except json.JSONDecodeError:
            basic = None
    dto = _to_asset_dto(a, basic=basic)
    dto["plugin_results"] = _plugin_results(session, a.id)
    return dto


def _asset_path(a: Asset) -> Path | None:
    """Resolve an asset's on-disk file from its ``path`` column.

    Two layouts are supported:
      * scanned media:   ``{media_root}\0{relative}``
      * uploaded media:  ``uploads\0{absolute_staging_path}``
    Returns ``None`` when the file cannot be resolved or is not a regular file.
    """
    if "\0" not in a.path:
        return None
    kind, _, rest = a.path.partition("\0")
    if kind == "uploads":
        p = Path(rest)
        if p.is_file():
            return p
        return None
    root = Path(kind)
    # Guard against path traversal — resolved must stay under the media root.
    try:
        resolved = (root / rest).resolve()
    except OSError:
        return None
    if resolved.is_file() and resolved.is_relative_to(root.resolve()):
        return resolved
    return None


@router.get("/assets/{asset_id}/file", summary="Stream an asset's original file (read-only)")
def asset_file(asset_id: int, session: Session = Depends(get_db)):
    a = session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    p = _asset_path(a)
    if p is None:
        raise HTTPException(404, "file not found on disk")
    media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return FileResponse(
        p,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff"},
    )
