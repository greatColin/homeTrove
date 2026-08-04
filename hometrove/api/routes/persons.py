"""Person management API.

People are grouped automatically by ``face.match``; this router lets an
operator rename them, attach free-form ``info`` (JSON), list photos per
person, merge duplicates, and trigger a naming backfill sweep.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.faces import merge_persons, name_person_and_backfill
from hometrove.models import FaceEmbedding, Person


router = APIRouter(prefix="/api/persons", tags=["persons"])


def _person_dto(p: Person, face_count: int | None = None, asset_ids: list[int] | None = None) -> dict:
    info: dict = {}
    try:
        info = json.loads(p.info_json or "{}")
    except json.JSONDecodeError:
        info = {}
    return {
        "id": p.id,
        "name": p.name,
        "info": info,
        "face_count": face_count,
        "asset_ids": asset_ids or [],
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("")
def list_persons(
    include_assets: bool = Query(False, description="include per-person asset ids"),
    session: Session = Depends(get_db),
):
    rows = session.execute(select(Person).order_by(Person.id)).scalars().all()
    items: list[dict] = []
    for p in rows:
        face_count = len(p.faces)
        asset_ids = None
        if include_assets:
            asset_ids = sorted({f.asset_id for f in p.faces})
        items.append(_person_dto(p, face_count=face_count, asset_ids=asset_ids))
    return {"items": items}


@router.get("/{person_id}")
def get_person(person_id: int, session: Session = Depends(get_db)):
    p = session.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    asset_ids = sorted({f.asset_id for f in p.faces})
    return _person_dto(p, face_count=len(p.faces), asset_ids=asset_ids)


class UpdatePerson(BaseModel):
    name: Optional[str] = None
    info: Optional[dict] = None
    backfill: bool = True


@router.patch("/{person_id}")
def update_person(person_id: int, body: UpdatePerson, session: Session = Depends(get_db)):
    p = session.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    moved = 0
    if body.info is not None:
        p.info_json = json.dumps(body.info, ensure_ascii=False)
        p.updated_at = int(time.time())
        session.commit()
    if body.name is not None and body.name != p.name:
        if body.name.strip():
            moved = name_person_and_backfill(session, p, body.name.strip())
    elif body.backfill and p.name and not p.name.startswith("未命名"):
        # Re-save triggers a fresh backfill even without a rename.
        moved = name_person_and_backfill(session, p, p.name)
    p = session.get(Person, person_id)
    return {**_person_dto(p, face_count=len(p.faces)), "backfilled": moved}


class MergePersons(BaseModel):
    keep_id: int
    remove_id: int


@router.post("/merge")
def merge(body: MergePersons, session: Session = Depends(get_db)):
    try:
        moved = merge_persons(session, body.keep_id, body.remove_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "moved": moved}


@router.get("/{person_id}/faces")
def person_faces(person_id: int, session: Session = Depends(get_db)):
    p = session.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    return {
        "faces": [
            {
                "id": f.id,
                "asset_id": f.asset_id,
                "confidence": f.confidence,
                "box": json.loads(f.box_json) if f.box_json else None,
            }
            for f in p.faces
        ]
    }
