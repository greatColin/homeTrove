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


def _person_dto(
    p: Person,
    face_count: int | None = None,
    cluster_count: int | None = None,
    asset_ids: list[int] | None = None,
    clusters: list[dict] | None = None,
) -> dict:
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
        "cluster_count": cluster_count,
        "asset_ids": asset_ids or [],
        "clusters": clusters or [],
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


def _person_face_count(p: Person) -> int:
    """Sum faces across every cluster attached to this person."""
    return sum(c.face_count for c in p.clusters)


def _person_asset_ids(p: Person) -> list[int]:
    """Distinct asset ids that contributed at least one face."""
    out: set[int] = set()
    for c in p.clusters:
        for f in c.faces:
            out.add(f.asset_id)
    return sorted(out)


def _person_clusters_dto(p: Person) -> list[dict]:
    return [
        {
            "id": c.id,
            "name": c.name,
            "source_plugin_id": c.source_plugin_id,
            "source_model_name": c.source_model_name,
            "face_count": c.face_count,
            "radius": c.radius,
        }
        for c in p.clusters
    ]


@router.get("")
def list_persons(
    include_assets: bool = Query(False, description="include per-person asset ids"),
    include_clusters: bool = Query(
        False, description="include the per-person cluster list"
    ),
    session: Session = Depends(get_db),
):
    rows = session.execute(select(Person).order_by(Person.id)).scalars().all()
    items: list[dict] = []
    for p in rows:
        face_count = _person_face_count(p)
        cluster_count = len(p.clusters)
        asset_ids = _person_asset_ids(p) if include_assets else None
        clusters = _person_clusters_dto(p) if include_clusters else None
        items.append(
            _person_dto(
                p,
                face_count=face_count,
                cluster_count=cluster_count,
                asset_ids=asset_ids,
                clusters=clusters,
            )
        )
    return {"items": items}


@router.get("/{person_id}")
def get_person(
    person_id: int,
    include_clusters: bool = Query(
        False, description="include the per-person cluster list"
    ),
    session: Session = Depends(get_db),
):
    p = session.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    return _person_dto(
        p,
        face_count=_person_face_count(p),
        cluster_count=len(p.clusters),
        asset_ids=_person_asset_ids(p),
        clusters=_person_clusters_dto(p) if include_clusters else None,
    )


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
    return {
        **_person_dto(
            p,
            face_count=_person_face_count(p),
            cluster_count=len(p.clusters),
        ),
        "backfilled": moved,
    }


class MergePersons(BaseModel):
    keep_id: int
    remove_id: int


class CreatePerson(BaseModel):
    name: str


@router.post("")
def create_person(body: CreatePerson, session: Session = Depends(get_db)):
    """Create a new person with the given name.

    Use this to seed a person before assigning clusters to them,
    or to create a person from an unassigned cluster's face.
    """
    person = Person(name=body.name.strip() or "未命名")
    session.add(person)
    session.flush()
    session.commit()
    return _person_dto(
        person,
        face_count=0,
        cluster_count=0,
    )


@router.post("/merge")
def merge(body: MergePersons, session: Session = Depends(get_db)):
    try:
        moved = merge_persons(session, body.keep_id, body.remove_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "moved": moved}


@router.get("/{person_id}/faces")
def person_faces(person_id: int, session: Session = Depends(get_db)):
    """All faces for a person (across every attached cluster)."""
    p = session.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    faces: list[dict] = []
    for c in p.clusters:
        for f in c.faces:
            box = None
            if f.box_json:
                try:
                    box = json.loads(f.box_json)
                except json.JSONDecodeError:
                    box = None
            faces.append(
                {
                    "id": f.id,
                    "asset_id": f.asset_id,
                    "confidence": f.confidence,
                    "box": box,
                    "cluster_id": c.id,
                    "source_plugin_id": f.source_plugin_id,
                    "source_model_name": f.source_model_name,
                    "frame_index": f.frame_index,
                    "frame_t": f.frame_t,
                }
            )
    return {"faces": faces}
