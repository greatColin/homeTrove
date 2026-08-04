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
    session: Session = Depends(get_db),
):
    stmt = select(Asset)
    if media_type:
        stmt = stmt.where(Asset.media_type == media_type)
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
    return _to_asset_dto(a, basic=basic)


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
