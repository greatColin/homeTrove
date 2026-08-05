"""Album management API.

Albums are manually curated collections of assets (immich/photoprism style).
An asset may belong to any number of albums; membership is stored in
``album_assets`` with a per-album ordinal used to preserve the owner's order.
"""

from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import Album, AlbumAsset, Asset


router = APIRouter(prefix="/api/albums", tags=["albums"])


def _album_dto(
    album: Album,
    asset_count: int | None = None,
    asset_ids: list[int] | None = None,
) -> dict:
    return {
        "id": album.id,
        "name": album.name,
        "description": album.description,
        "cover_asset_id": album.cover_asset_id,
        "asset_count": asset_count if asset_count is not None else len(album.items),
        "asset_ids": asset_ids or [],
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


@router.post("", status_code=201)
def create_album(body: CreateAlbum, session: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "album name must not be blank")
    album = Album(name=name, description=body.description)
    session.add(album)
    session.flush()
    position = 0
    for aid in dict.fromkeys(body.asset_ids):
        if session.get(Asset, aid) is None:
            raise HTTPException(400, f"asset {aid} not found")
        session.add(AlbumAsset(album_id=album.id, asset_id=aid, position=position))
        position += 1
    session.commit()
    session.refresh(album)
    ids = [it.asset_id for it in album.items]
    return _album_dto(album, asset_count=len(ids), asset_ids=ids)


@router.get("/{album_id}")
def get_album(album_id: int, session: Session = Depends(get_db)):
    a = session.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "album not found")
    ids = [it.asset_id for it in a.items]
    return _album_dto(a, asset_count=len(ids), asset_ids=ids)


class UpdateAlbum(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_asset_id: Optional[int] = None


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
    a.updated_at = int(time.time())
    session.commit()
    session.refresh(a)
    ids = [it.asset_id for it in a.items]
    return _album_dto(a, asset_count=len(ids), asset_ids=ids)


class AlbumAssets(BaseModel):
    asset_ids: list[int]


@router.post("/{album_id}/assets")
def add_assets(album_id: int, body: AlbumAssets, session: Session = Depends(get_db)):
    a = session.get(Album, album_id)
    if a is None:
        raise HTTPException(404, "album not found")
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
