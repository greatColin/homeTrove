"""Face-cluster management API.

A ``FaceCluster`` is one auto-detected identity emitted by a single plugin
+ model pair (``source_plugin_id``, ``source_model_name``). Clusters are
created automatically by the worker; this router exposes them for the UI
to display, rename, attach to a Person, merge, or delete.

Endpoints:

* ``GET    /api/clusters``                  list clusters (filterable by person)
* ``GET    /api/clusters/{id}``             cluster detail + representative_face
* ``PATCH  /api/clusters/{id}``             rename or change person_id
* ``DELETE /api/clusters/{id}``             drop the cluster and its faces
* ``POST   /api/clusters/{id}/faces``       reassign a single face to this cluster
* ``POST   /api/clusters/{src}/merge-into/{dst}``  merge src → dst
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
from hometrove.models import FaceCluster, FaceEmbedding, Person


router = APIRouter(prefix="/api/clusters", tags=["clusters"])


def _face_dto(f: FaceEmbedding) -> dict:
    box = None
    if f.box_json:
        try:
            box = json.loads(f.box_json)
        except json.JSONDecodeError:
            box = None
    return {
        "id": f.id,
        "asset_id": f.asset_id,
        "asset_filename": f.asset.filename if f.asset else None,
        "confidence": f.confidence,
        "box": box,
        "frame_index": f.frame_index,
        "frame_t": f.frame_t,
    }


def _cluster_dto(
    c: FaceCluster,
    *,
    faces: Optional[list[FaceEmbedding]] = None,
    representative_face: Optional[FaceEmbedding] = None,
) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "person_id": c.person_id,
        "source_plugin_id": c.source_plugin_id,
        "source_model_name": c.source_model_name,
        "face_count": c.face_count,
        "radius": c.radius,
        "representative_face": _face_dto(representative_face)
        if representative_face is not None
        else None,
        "faces": [_face_dto(f) for f in (faces or [])],
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _load_representative(session: Session, c: FaceCluster) -> Optional[FaceEmbedding]:
    if c.representative_face_id is None:
        return None
    return session.get(FaceEmbedding, c.representative_face_id)


@router.get("")
def list_clusters(
    person_id: Optional[int] = Query(None, description="filter by person_id"),
    unassigned: bool = Query(False, description="only show clusters with no Person"),
    source_plugin_id: Optional[str] = Query(
        None, description="filter by source plugin id (e.g. face.image)"
    ),
    session: Session = Depends(get_db),
):
    stmt = select(FaceCluster).order_by(FaceCluster.id.desc())
    if person_id is not None:
        stmt = stmt.where(FaceCluster.person_id == person_id)
    elif unassigned:
        stmt = stmt.where(FaceCluster.person_id.is_(None))
    if source_plugin_id is not None:
        stmt = stmt.where(FaceCluster.source_plugin_id == source_plugin_id)
    rows = session.execute(stmt).scalars().all()
    return {
        "items": [
            _cluster_dto(c, representative_face=_load_representative(session, c))
            for c in rows
        ]
    }


@router.get("/{cluster_id}")
def get_cluster(cluster_id: int, session: Session = Depends(get_db)):
    c = session.get(FaceCluster, cluster_id)
    if c is None:
        raise HTTPException(404, "cluster not found")
    faces = list(c.faces)
    return _cluster_dto(
        c,
        faces=faces,
        representative_face=_load_representative(session, c),
    )


class UpdateCluster(BaseModel):
    name: Optional[str] = None
    person_id: Optional[int] = None
    clear_person: bool = False


@router.patch("/{cluster_id}")
def update_cluster(
    cluster_id: int, body: UpdateCluster, session: Session = Depends(get_db)
):
    c = session.get(FaceCluster, cluster_id)
    if c is None:
        raise HTTPException(404, "cluster not found")
    if body.name is not None and body.name.strip():
        c.name = body.name.strip()
    if body.clear_person:
        c.person_id = None
    elif body.person_id is not None:
        target = session.get(Person, body.person_id)
        if target is None:
            raise HTTPException(404, "person not found")
        c.person_id = body.person_id
    c.updated_at = int(time.time())
    session.commit()
    return _cluster_dto(c, representative_face=_load_representative(session, c))


@router.delete("/{cluster_id}")
def delete_cluster(cluster_id: int, session: Session = Depends(get_db)):
    c = session.get(FaceCluster, cluster_id)
    if c is None:
        raise HTTPException(404, "cluster not found")
    session.delete(c)
    session.commit()
    return {"ok": True, "deleted": cluster_id}


class AddFace(BaseModel):
    face_id: int


@router.post("/{cluster_id}/faces")
def add_face(
    cluster_id: int, body: AddFace, session: Session = Depends(get_db)
):
    """Reassign a single face to this cluster.

    This is the manual correction path: when a single face was misrouted by
    the auto-cluster pipeline, the user can move it without touching
    anything else.
    """
    c = session.get(FaceCluster, cluster_id)
    if c is None:
        raise HTTPException(404, "cluster not found")
    face = session.get(FaceEmbedding, body.face_id)
    if face is None:
        raise HTTPException(404, "face not found")
    if face.source_plugin_id != c.source_plugin_id:
        raise HTTPException(
            400,
            "cannot move face across source_plugin partitions",
        )
    if face.source_model_name != c.source_model_name:
        raise HTTPException(
            400,
            "cannot move face across source_model partitions",
        )
    face.cluster_id = cluster_id
    c.face_count = sum(1 for _ in c.faces)
    c.updated_at = int(time.time())
    session.commit()
    return _cluster_dto(c, representative_face=_load_representative(session, c))


@router.post("/{src_id}/merge-into/{dst_id}")
def merge_clusters(
    src_id: int, dst_id: int, session: Session = Depends(get_db)
):
    """Merge src → dst. All faces in src are reassigned to dst and src is
    deleted. The two clusters must belong to the same ``(source_plugin_id,
    source_model_name)`` partition — cross-plugin merges are a manual UI
    operation handled at the Person level, not here.
    """
    if src_id == dst_id:
        raise HTTPException(400, "src and dst must differ")
    src = session.get(FaceCluster, src_id)
    dst = session.get(FaceCluster, dst_id)
    if src is None or dst is None:
        raise HTTPException(404, "cluster not found")
    if (src.source_plugin_id, src.source_model_name) != (
        dst.source_plugin_id,
        dst.source_model_name,
    ):
        raise HTTPException(
            400,
            "cannot merge clusters across source_plugin partitions; "
            "use POST /api/persons/{keep_id}/merge instead",
        )
    moved = 0
    for f in list(src.faces):
        f.cluster_id = dst_id
        moved += 1
    dst.face_count = sum(1 for _ in dst.faces)
    dst.updated_at = int(time.time())
    session.delete(src)
    session.commit()
    return {"ok": True, "moved": moved, "deleted_cluster_id": src_id}