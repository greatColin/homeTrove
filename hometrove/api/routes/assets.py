from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from hometrove.config import get_settings
from hometrove.db import get_db
from hometrove.models import Album, AlbumAsset, Asset, AsrTranscript, FaceEmbedding, PluginResult
from hometrove.smart_albums import _place_ids as _smart_place_ids
from hometrove import trash as _trash


router = APIRouter(prefix="/api", tags=["assets"])


# Which plugin result feeds which facet filter. Keyed by the facet name used
# in ``/api/assets?tags=..`` etc.; the value is the plugin id that produces it.
_FACET_PLUGIN = {
    "tags": "mock.tags",
    "category": "mock.category",
}


def _facet_asset_ids(session: Session, facet: str, value: str) -> list[int]:
    """Asset ids whose facet plugin result contains ``value``.

    Uses a JSON substring match on ``result_json`` — precise enough for M0's
    small libraries and works on any SQL backend. The facet plugins write
    values as JSON string keys. The ``person`` facet is handled separately via
    ``face_embeddings`` (persons are matched, not detected).
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


def _person_asset_ids(session: Session, person_id: int) -> list[int]:
    rows = session.execute(
        select(FaceEmbedding.asset_id).where(
            FaceEmbedding.person_id == person_id
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


def _transcripts(session: Session, asset_id: int) -> list[dict]:
    """All ASR segments for the asset, ordered by ``t_start``.

    Joined with the source plugin (``asr.faster_whisper`` today) so the
    frontend's video player can both display the cues and seek to the
    matching second.
    """
    rows = (
        session.execute(
            select(AsrTranscript)
            .where(AsrTranscript.asset_id == asset_id)
            .order_by(AsrTranscript.t_start)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": row.id,
            "plugin_id": row.plugin_id,
            "plugin_version": row.plugin_version,
            "t_start": row.t_start,
            "t_end": row.t_end,
            "text": row.text,
            "lang": row.lang,
            "confidence": row.confidence,
        }
        for row in rows
    ]


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
        "deleted_at": a.deleted_at,
        "favorite": bool(a.favorite),
        "basic_info": basic,
    }


@router.get("/assets")
def list_assets(
    media_type: Optional[str] = Query(None, pattern="^(image|video|other)$"),
    cursor: Optional[int] = None,
    limit: int = Query(60, ge=1, le=500),
    tag: Optional[str] = None,
    category: Optional[str] = None,
    person_id: Optional[int] = None,
    favorite: Optional[bool] = Query(None, description="v1 favorite: when true, only favorited assets; when false, only non-favorites; when omitted, no filter."),
    taken_after: Optional[int] = Query(None, ge=0, description="v1 advanced filters: only assets with taken_at >= this epoch."),
    taken_before: Optional[int] = Query(None, ge=0, description="v1 advanced filters: only assets with taken_at < this epoch."),
    place: Optional[str] = Query(None, pattern=r"^-?\d+\.?\d*,-?\d+\.?\d*$", description="v1 advanced filters: place grid cell centre as 'lat,lon'."),
    include_trashed: bool = Query(False, description="v1 trash: include soft-deleted assets in the list. Default false so the user-visible library hides them."),
    session: Session = Depends(get_db),
):
    stmt = select(Asset)
    # v1 trash: hide soft-deleted assets from the default library view.
    # Power-users can pass ``include_trashed=true`` to inspect trash state.
    if not include_trashed:
        stmt = stmt.where(Asset.deleted_at.is_(None))
    if media_type:
        stmt = stmt.where(Asset.media_type == media_type)
    if favorite is not None:
        stmt = stmt.where(Asset.favorite == (1 if favorite else 0))
    if taken_after is not None:
        stmt = stmt.where(Asset.taken_at >= taken_after)
    if taken_before is not None:
        stmt = stmt.where(Asset.taken_at < taken_before)

    # Facet filters narrow the result set to assets whose plugin output
    # contains the selected value. ``person_id`` filters via face_embeddings.
    facet_ids: set[int] | None = None
    for facet, value in (("tags", tag), ("category", category)):
        if value:
            ids = set(_facet_asset_ids(session, facet, value))
            facet_ids = ids if facet_ids is None else (facet_ids & ids)
    if person_id is not None:
        ids = set(_person_asset_ids(session, person_id))
        facet_ids = ids if facet_ids is None else (facet_ids & ids)
    if place is not None:
        ids = _smart_place_ids(session, place)
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
def get_asset(
    asset_id: int,
    include_trashed: bool = Query(False, description="v1 trash: when true, return soft-deleted assets too. Default false."),
    session: Session = Depends(get_db),
):
    a = session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    if a.deleted_at is not None and not include_trashed:
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
    dto["transcripts"] = _transcripts(session, a.id)
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
    if a is None or a.deleted_at is not None:
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


@router.get("/assets/{asset_id}/thumbnail", summary="Serve a generated thumbnail")
def asset_thumbnail(
    asset_id: int,
    size: str = Query("small", pattern="^(small|medium|placeholder)$"),
    session: Session = Depends(get_db),
):
    """Return a generated thumbnail for an asset from ``{data_dir}/thumbs/``.

    ``size`` selects the bucket written by the ``thumbnail`` plugin. Falls back
    to the original file only when no thumbnail exists yet and the asset is an
    image — this keeps the grid usable while jobs are still queued.
    """
    a = session.get(Asset, asset_id)
    if a is None or a.deleted_at is not None:
        raise HTTPException(404, "asset not found")

    thumbs_dir = get_settings().resolved_data_dir() / "thumbs" / str(a.id)
    candidates = [thumbs_dir / f"{size}.jpg"]
    if size == "placeholder":
        candidates.insert(0, thumbs_dir / "_frame.png")
    for p in candidates:
        if p.is_file():
            return FileResponse(p, media_type="image/jpeg")
    if size == "placeholder":
        if (thumbs_dir / "_frame.png").is_file():
            return FileResponse(thumbs_dir / "_frame.png", media_type="image/png")

    # No thumbnail yet: for images serve the original; for anything else 404
    # and let the frontend show its labeled tile.
    if a.media_type == "image":
        p = _asset_path(a)
        if p is not None:
            media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return FileResponse(p, media_type=media_type)
    raise HTTPException(404, "thumbnail not found")


# ---------------------------------------------------------------------------
# v1 trash: soft delete + restore + permanent empty.
# ---------------------------------------------------------------------------


@router.post("/assets/{asset_id}/trash", summary="Move an asset to trash (soft delete)")
def trash_asset(asset_id: int, session: Session = Depends(get_db)):
    try:
        result = _trash.move_to_trash(session, asset_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "id": result.asset_id, "deleted_at": result.deleted_at}


@router.post("/assets/{asset_id}/restore", summary="Restore an asset from trash")
def restore_asset(asset_id: int, session: Session = Depends(get_db)):
    try:
        result = _trash.restore_from_trash(session, asset_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "id": result.asset_id, "deleted_at": result.deleted_at}


@router.get("/trash", summary="List assets in the trash (newest deletion first)")
def list_trash(
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
):
    rows, total = _trash.list_trashed(session, limit=limit, offset=offset)
    return {
        "items": [_to_asset_dto(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/trash/empty", summary="Permanently drop trashed assets from the database")
def empty_trash(
    older_than_seconds: Optional[int] = Query(
        None,
        ge=0,
        description="If omitted, empties the entire trash. If set, only drops rows whose deleted_at is older than this many seconds.",
    ),
    session: Session = Depends(get_db),
):
    dropped = _trash.empty_trash(session, older_than_seconds=older_than_seconds)
    return {"ok": True, "dropped": dropped}


# ---------------------------------------------------------------------------
# v1 favorites: per-asset toggle. Bulk favorite lives under /assets/bulk.
# ---------------------------------------------------------------------------


@router.post("/assets/{asset_id}/favorite", summary="Toggle the favorite flag on a single asset")
def toggle_favorite(asset_id: int, session: Session = Depends(get_db)):
    """Flip ``Asset.favorite`` between 0 and 1. Idempotent: calling twice
    restores the original state. Returns the new value so the UI can sync
    without an extra round-trip."""
    a = session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    a.favorite = 0 if a.favorite else 1
    a.updated_at = int(__import__("time").time())
    session.commit()
    return {"ok": True, "id": a.id, "favorite": bool(a.favorite)}


@router.put("/assets/{asset_id}/favorite", summary="Set the favorite flag explicitly")
def set_favorite(asset_id: int, session: Session = Depends(get_db)):
    """Set ``Asset.favorite`` to 1 (separate endpoint to make the user
    intent explicit and reversible from the UI without stateful toggling)."""
    a = session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    a.favorite = 1
    a.updated_at = int(__import__("time").time())
    session.commit()
    return {"ok": True, "id": a.id, "favorite": True}


@router.delete("/assets/{asset_id}/favorite", summary="Clear the favorite flag")
def clear_favorite(asset_id: int, session: Session = Depends(get_db)):
    a = session.get(Asset, asset_id)
    if a is None:
        raise HTTPException(404, "asset not found")
    a.favorite = 0
    a.updated_at = int(__import__("time").time())
    session.commit()
    return {"ok": True, "id": a.id, "favorite": False}


# ---------------------------------------------------------------------------
# v1 bulk: multi-asset mutations in one request. All accept a body of the
# shape ``{"asset_ids": [int, ...]}`` and report a ``requested`` /
# ``affected`` / ``missing`` breakdown so the UI can show partial-failure
# counts without a per-asset error table.
# ---------------------------------------------------------------------------


# Cap keeps a malicious / buggy client from chewing through the DB.
_BULK_MAX = 1000


class _BulkBody(BaseModel):
    asset_ids: list[int] = Field(..., description="Asset ids to operate on.")


class _BulkAddToAlbumBody(BaseModel):
    asset_ids: list[int] = Field(...)
    album_id: int = Field(..., description="Album to append the assets to (must already exist).")


def _bulk_resolve(session: Session, asset_ids: list[int]) -> tuple[list[Asset], list[int]]:
    """Resolve ``asset_ids`` to live ``Asset`` rows.

    Returns ``(rows, missing)``: rows are the assets that exist (trashed or
    live, the caller decides which to operate on), missing is the list of
    ids that don't exist at all. De-duplicates input.
    """
    if not asset_ids:
        return [], []
    seen: set[int] = set()
    ordered: list[int] = []
    for aid in asset_ids:
        if aid not in seen:
            seen.add(aid)
            ordered.append(aid)
    existing = session.execute(
        select(Asset).where(Asset.id.in_(ordered))
    ).scalars().all()
    found = {a.id: a for a in existing}
    missing = [aid for aid in ordered if aid not in found]
    return list(found.values()), missing


@router.post("/bulk/assets/trash", summary="Soft-delete many assets in one request")
def bulk_trash(body: _BulkBody, session: Session = Depends(get_db)):
    """Move every existing asset in ``asset_ids`` to trash. Trashed assets
    that are passed again are no-ops (idempotent — the original
    ``deleted_at`` is preserved).
    """
    if len(body.asset_ids) > _BULK_MAX:
        raise HTTPException(400, f"asset_ids exceeds max of {_BULK_MAX}")
    rows, missing = _bulk_resolve(session, body.asset_ids)
    now = int(__import__("time").time())
    affected = 0
    for a in rows:
        if a.deleted_at is None:
            a.deleted_at = now
            a.updated_at = now
            affected += 1
    session.commit()
    return {
        "ok": True,
        "requested": len(set(body.asset_ids)),
        "affected": affected,
        "missing": missing,
    }


@router.post("/bulk/assets/restore", summary="Restore many assets from trash")
def bulk_restore(body: _BulkBody, session: Session = Depends(get_db)):
    if len(body.asset_ids) > _BULK_MAX:
        raise HTTPException(400, f"asset_ids exceeds max of {_BULK_MAX}")
    rows, missing = _bulk_resolve(session, body.asset_ids)
    now = int(__import__("time").time())
    affected = 0
    for a in rows:
        if a.deleted_at is not None:
            a.deleted_at = None
            a.updated_at = now
            affected += 1
    session.commit()
    return {
        "ok": True,
        "requested": len(set(body.asset_ids)),
        "affected": affected,
        "missing": missing,
    }


@router.post("/bulk/assets/favorite", summary="Mark many assets as favorites")
def bulk_favorite(body: _BulkBody, session: Session = Depends(get_db)):
    if len(body.asset_ids) > _BULK_MAX:
        raise HTTPException(400, f"asset_ids exceeds max of {_BULK_MAX}")
    rows, missing = _bulk_resolve(session, body.asset_ids)
    now = int(__import__("time").time())
    affected = 0
    for a in rows:
        if a.favorite != 1:
            a.favorite = 1
            a.updated_at = now
            affected += 1
    session.commit()
    return {
        "ok": True,
        "requested": len(set(body.asset_ids)),
        "affected": affected,
        "missing": missing,
    }


@router.post("/bulk/assets/unfavorite", summary="Clear favorite flag on many assets")
def bulk_unfavorite(body: _BulkBody, session: Session = Depends(get_db)):
    if len(body.asset_ids) > _BULK_MAX:
        raise HTTPException(400, f"asset_ids exceeds max of {_BULK_MAX}")
    rows, missing = _bulk_resolve(session, body.asset_ids)
    now = int(__import__("time").time())
    affected = 0
    for a in rows:
        if a.favorite != 0:
            a.favorite = 0
            a.updated_at = now
            affected += 1
    session.commit()
    return {
        "ok": True,
        "requested": len(set(body.asset_ids)),
        "affected": affected,
        "missing": missing,
    }


@router.post("/bulk/assets/add-to-album", summary="Append many assets to an album")
def bulk_add_to_album(body: _BulkAddToAlbumBody, session: Session = Depends(get_db)):
    """Append ``asset_ids`` to ``album_id`` preserving the existing order
    (existing members keep their position; new members are appended in the
    order they appear in the request body). Returns ``added`` so the UI can
    surface a partial-success toast.
    """
    if len(body.asset_ids) > _BULK_MAX:
        raise HTTPException(400, f"asset_ids exceeds max of {_BULK_MAX}")
    album = session.get(Album, body.album_id)
    if album is None:
        raise HTTPException(404, f"album {body.album_id} not found")
    rows, missing = _bulk_resolve(session, body.asset_ids)
    existing = {it.asset_id for it in album.items}
    position = max((it.position for it in album.items), default=-1) + 1
    added = 0
    for a in rows:
        if a.id in existing:
            continue
        session.add(
            AlbumAsset(album_id=album.id, asset_id=a.id, position=position)
        )
        position += 1
        existing.add(a.id)
        added += 1
    if added:
        album.updated_at = int(__import__("time").time())
    session.commit()
    return {
        "ok": True,
        "added": added,
        "requested": len(set(body.asset_ids)),
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# v1 shared albums: public, token-based access to curated albums. The owner
# creates an opaque token with permission flags and an optional expiration.
# Public endpoints below do not require authentication but still resolve a
# principal so rate-limiting / logging remains consistent.
# ---------------------------------------------------------------------------

import secrets as _secrets

from hometrove.models import AlbumShare
from hometrove.smart_albums import eval_rule


def _live_share(session: Session, token: str) -> AlbumShare:
    """Return the share link if it exists and has not expired."""
    row = session.execute(
        select(AlbumShare).where(AlbumShare.token == token)
    ).scalars().first()
    now = int(__import__("time").time())
    if row is None or (row.expires_at is not None and row.expires_at <= now):
        raise HTTPException(404, "share not found or expired")
    return row


def _shared_asset_ids(session: Session, share: AlbumShare) -> list[int]:
    """Ordered list of live asset ids in the shared album."""
    album = share.album
    if album.is_smart:
        rule = album.smart_rule
        if rule is None:
            return []
        return eval_rule(session, json.loads(rule.rule_json))
    return [
        it.asset_id
        for it in album.items
        if it.asset.deleted_at is None
    ]


def _shared_asset(session: Session, share: AlbumShare, asset_id: int) -> Asset:
    """Return the asset if it is a live member of the shared album."""
    album = share.album
    if album.is_smart:
        ids = set(_shared_asset_ids(session, share))
        if asset_id not in ids:
            raise HTTPException(404, "asset not found in share")
        a = session.get(Asset, asset_id)
        if a is None or a.deleted_at is not None:
            raise HTTPException(404, "asset not found in share")
        return a
    for it in album.items:
        if it.asset_id == asset_id and it.asset.deleted_at is None:
            return it.asset
    raise HTTPException(404, "asset not found in share")


@router.get("/public/albums/{token}", summary="View a shared album without authentication")
def public_album(token: str, session: Session = Depends(get_db)):
    share = _live_share(session, token)
    asset_ids = _shared_asset_ids(session, share)
    return {
        "id": share.album.id,
        "name": share.album.name,
        "description": share.album.description,
        "cover_asset_id": share.album.cover_asset_id,
        "allow_original": bool(share.allow_original),
        "allow_download": bool(share.allow_download),
        "expires_at": share.expires_at,
        "created_at": share.created_at,
        "asset_ids": asset_ids,
    }


@router.get("/public/thumbnails/{token}/{asset_id}/{size}", summary="Thumbnail for a shared album asset")
def public_thumbnail(
    token: str,
    asset_id: int,
    size: str,
    session: Session = Depends(get_db),
):
    """Return a generated thumbnail for a shared album asset.

    ``size`` is a path parameter selecting the bucket written by the
    ``thumbnail`` plugin. Fallback to the original image when no thumbnail
    exists yet, matching the authenticated endpoint behaviour.
    """
    if size not in ("small", "medium", "placeholder"):
        raise HTTPException(422, "size must be one of small, medium, placeholder")
    share = _live_share(session, token)
    a = _shared_asset(session, share, asset_id)

    thumbs_dir = get_settings().resolved_data_dir() / "thumbs" / str(a.id)
    candidates = [thumbs_dir / f"{size}.jpg"]
    if size == "placeholder":
        candidates.insert(0, thumbs_dir / "_frame.png")
    for p in candidates:
        if p.is_file():
            return FileResponse(p, media_type="image/jpeg")
    if size == "placeholder" and (thumbs_dir / "_frame.png").is_file():
        return FileResponse(thumbs_dir / "_frame.png", media_type="image/png")

    if a.media_type == "image":
        p = _asset_path(a)
        if p is not None:
            media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            return FileResponse(p, media_type=media_type)
    raise HTTPException(404, "thumbnail not found")


@router.get("/public/files/{token}/{asset_id}", summary="Original file for a shared album asset")
def public_file(
    token: str,
    asset_id: int,
    session: Session = Depends(get_db),
):
    share = _live_share(session, token)
    if not share.allow_original:
        raise HTTPException(403, "original access not allowed")
    a = _shared_asset(session, share, asset_id)
    p = _asset_path(a)
    if p is None:
        raise HTTPException(404, "file not found on disk")
    media_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    headers = {"X-Content-Type-Options": "nosniff"}
    filename = p.name
    if share.allow_download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return FileResponse(
        p,
        media_type=media_type,
        filename=filename if share.allow_download else None,
        headers=headers,
    )
