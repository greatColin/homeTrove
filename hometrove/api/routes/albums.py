"""Album management API.

Albums are manually curated collections of assets (immich/photoprism style).
An asset may belong to any number of albums; membership is stored in
``album_assets`` with a per-album ordinal used to preserve the owner's order.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.api.deps import current_principal
from hometrove.auth import Principal
from hometrove.db import get_db
from hometrove.models import Album, AlbumAsset, AlbumShare, Asset, SmartAlbumRule
from hometrove.smart_albums import eval_rule, validate_rule


router = APIRouter(prefix="/api/albums", tags=["albums"])


def _album_dto(
    album: Album,
    asset_count: int | None = None,
    asset_ids: list[int] | None = None,
    rule: dict | None = None,
) -> dict:
    return {
        "id": album.id,
        "name": album.name,
        "description": album.description,
        "cover_asset_id": album.cover_asset_id,
        "is_smart": bool(album.is_smart),
        "asset_count": asset_count if asset_count is not None else len(album.items),
        "asset_ids": asset_ids or [],
        "rule": rule,
        "created_at": album.created_at,
        "updated_at": album.updated_at,
    }


@router.get("")
def list_albums(
    include_assets: bool = False,
    session: Session = Depends(get_db),
):
    rows = session.execute(select(Album).order_by(Album.id)).scalars().all()
    items = []
    for a in rows:
        if a.is_smart:
            rule = a.smart_rule
            ids = eval_rule(session, json.loads(rule.rule_json)) if rule else []
            items.append(_album_dto(a, asset_count=len(ids), asset_ids=ids if include_assets else None))
        else:
            ids = [it.asset_id for it in a.items]
            items.append(
                _album_dto(
                    a,
                    asset_count=len(ids),
                    asset_ids=ids if include_assets else None,
                )
            )
    return {"items": items}


class CreateAlbum(BaseModel):
    name: str
    description: str = ""
    asset_ids: list[int] = []
    is_smart: bool = False
    rule: Optional[dict] = None


@router.post("", status_code=201)
def create_album(body: CreateAlbum, session: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "album name must not be blank")
    if body.is_smart and body.rule is None:
        raise HTTPException(422, "smart album requires a rule")
    if not body.is_smart and body.rule is not None:
        raise HTTPException(422, "manual album cannot have a rule")
    if body.is_smart:
        validate_rule(body.rule)

    album = Album(name=name, description=body.description, is_smart=1 if body.is_smart else 0)
    session.add(album)
    session.flush()

    if body.is_smart:
        session.add(
            SmartAlbumRule(
                album_id=album.id,
                rule_json=json.dumps(body.rule),
                created_at=int(time.time()),
                updated_at=int(time.time()),
            )
        )
        ids = eval_rule(session, body.rule)
    else:
        position = 0
        for aid in dict.fromkeys(body.asset_ids):
            if session.get(Asset, aid) is None:
                raise HTTPException(400, f"asset {aid} not found")
            session.add(AlbumAsset(album_id=album.id, asset_id=aid, position=position))
            position += 1
        session.flush()
        ids = [it.asset_id for it in album.items]

    session.commit()
    session.refresh(album)
    rule_out = json.loads(album.smart_rule.rule_json) if album.smart_rule else None
    return _album_dto(album, asset_count=len(ids), asset_ids=ids, rule=rule_out)


@router.get("/{album_id}")
def get_album(album_id: int, session: Session = Depends(get_db)):
    a = session.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "album not found")
    if a.is_smart:
        rule = a.smart_rule
        ids = eval_rule(session, json.loads(rule.rule_json)) if rule else []
        rule_out = json.loads(rule.rule_json) if rule else None
        return _album_dto(a, asset_count=len(ids), asset_ids=ids, rule=rule_out)
    ids = [it.asset_id for it in a.items]
    return _album_dto(a, asset_count=len(ids), asset_ids=ids)


class UpdateAlbum(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_asset_id: Optional[int] = None
    rule: Optional[dict] = None


@router.patch("/{album_id}")
def update_album(album_id: int, body: UpdateAlbum, session: Session = Depends(get_db)):
    a = session.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "album not found")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "album name must not be blank")
        a.name = name
    if body.description is not None:
        a.description = body.description
    if body.cover_asset_id is not None:
        if session.get(Asset, body.cover_asset_id) is None:
            raise HTTPException(400, "cover asset not found")
        a.cover_asset_id = body.cover_asset_id
    if body.rule is not None:
        if not a.is_smart:
            raise HTTPException(422, "manual album cannot have a rule")
        validate_rule(body.rule)
        rule = a.smart_rule
        if rule is None:
            rule = SmartAlbumRule(album_id=a.id, rule_json=json.dumps(body.rule))
            session.add(rule)
        else:
            rule.rule_json = json.dumps(body.rule)
            rule.updated_at = int(time.time())
        session.flush()

    a.updated_at = int(time.time())
    session.commit()
    session.refresh(a)

    if a.is_smart:
        rule = a.smart_rule
        ids = eval_rule(session, json.loads(rule.rule_json)) if rule else []
        rule_out = json.loads(rule.rule_json) if rule else None
        return _album_dto(a, asset_count=len(ids), asset_ids=ids, rule=rule_out)
    ids = [it.asset_id for it in a.items]
    return _album_dto(a, asset_count=len(ids), asset_ids=ids)


class AlbumAssets(BaseModel):
    asset_ids: list[int]


@router.post("/{album_id}/assets")
def add_assets(album_id: int, body: AlbumAssets, session: Session = Depends(get_db)):
    a = session.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "album not found")
    if a.is_smart:
        raise HTTPException(400, "smart album membership is read-only")
    existing = {it.asset_id for it in a.items}
    position = max((it.position for it in a.items), default=-1) + 1
    added = 0
    for aid in dict.fromkeys(body.asset_ids):
        if aid in existing:
            continue
        if session.get(Asset, aid) is None:
            raise HTTPException(400, f"asset {aid} not found")
        session.add(AlbumAsset(album_id=album_id, asset_id=aid, position=position))
        position += 1
        added += 1
    if added:
        a.updated_at = int(time.time())
    session.commit()
    session.refresh(a)
    ids = [it.asset_id for it in a.items]
    return {"ok": True, "added": added, "album": _album_dto(a, asset_count=len(ids), asset_ids=ids)}


@router.delete("/{album_id}/assets")
def remove_assets(album_id: int, body: AlbumAssets, session: Session = Depends(get_db)):
    a = session.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "album not found")
    if a.is_smart:
        raise HTTPException(400, "smart album membership is read-only")
    remove = set(body.asset_ids)
    removed_items = [it for it in a.items if it.asset_id in remove]
    removed = len(removed_items)
    if removed:
        for it in removed_items:
            session.delete(it)
        session.flush()
        # Re-read the surviving members (the relationship collection still
        # holds the just-deleted instances until expired).
        session.expire(a, ["items"])
        for i, it in enumerate(a.items):
            it.position = i
            session.add(it)
        a.updated_at = int(time.time())
    session.commit()
    session.refresh(a)
    ids = [it.asset_id for it in a.items]
    return {"ok": True, "removed": removed, "album": _album_dto(a, asset_count=len(ids), asset_ids=ids)}


@router.delete("/{album_id}")
def delete_album(album_id: int, session: Session = Depends(get_db)):
    a = session.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "album not found")
    session.delete(a)
    session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# v1 shared albums: owner-managed share links for an album.
# ---------------------------------------------------------------------------

import secrets as _secrets


class CreateShare(BaseModel):
    allow_original: bool = False
    allow_download: bool = False
    expires_at: Optional[int] = None


class ShareOut(BaseModel):
    token: str
    allow_original: bool
    allow_download: bool
    expires_at: Optional[int]
    created_at: int
    share_url: str

    model_config = {"from_attributes": True}


@router.post("/{album_id}/shares", status_code=201, summary="Create a public share link for an album")
def create_share(
    album_id: int,
    body: CreateShare,
    session: Session = Depends(get_db),
    principal: Principal = Depends(current_principal),
):
    album = session.get(Album, album_id)
    if album is None:
        raise HTTPException(404, "album not found")

    now = int(time.time())
    if body.expires_at is not None and body.expires_at <= now:
        raise HTTPException(422, "expires_at must be in the future")

    for _ in range(10):
        token = _secrets.token_urlsafe(32)
        existing = session.execute(
            select(AlbumShare).where(AlbumShare.token == token)
        ).scalars().first()
        if existing is None:
            break
    else:
        raise HTTPException(500, "failed to generate unique share token")

    share = AlbumShare(
        album_id=album.id,
        token=token,
        allow_original=1 if body.allow_original else 0,
        allow_download=1 if body.allow_download else 0,
        expires_at=body.expires_at,
        created_at=now,
        created_by=principal.id,
    )
    session.add(share)
    session.commit()
    session.refresh(share)

    share_url = f"/share/{token}"
    return ShareOut(
        token=share.token,
        allow_original=bool(share.allow_original),
        allow_download=bool(share.allow_download),
        expires_at=share.expires_at,
        created_at=share.created_at,
        share_url=share_url,
    )


@router.get("/{album_id}/shares", summary="List active share links for an album")
def list_shares(album_id: int, session: Session = Depends(get_db)):
    album = session.get(Album, album_id)
    if album is None:
        raise HTTPException(404, "album not found")
    now = int(time.time())
    items = [
        {
            "token": s.token,
            "allow_original": bool(s.allow_original),
            "allow_download": bool(s.allow_download),
            "expires_at": s.expires_at,
            "created_at": s.created_at,
            "share_url": f"/share/{s.token}",
        }
        for s in album.shares
        if s.expires_at is None or s.expires_at >= now
    ]
    return {"items": items}


@router.delete("/{album_id}/shares/{token}", summary="Revoke a share link")
def delete_share(album_id: int, token: str, session: Session = Depends(get_db)):
    album = session.get(Album, album_id)
    if album is None:
        raise HTTPException(404, "album not found")
    share = session.execute(
        select(AlbumShare).where(
            AlbumShare.album_id == album_id,
            AlbumShare.token == token,
        )
    ).scalars().first()
    if share is None:
        raise HTTPException(404, "share not found")
    session.delete(share)
    session.commit()
    return {"ok": True}
