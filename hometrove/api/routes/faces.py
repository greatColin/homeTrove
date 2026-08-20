"""Face management API.

Faces are emitted by ``face.image`` / ``face.video`` and persisted by the
auto-cluster pipeline. Most user actions happen at the cluster level;
this router exposes single-face read and delete for the UI's "remove this
false positive" affordance and for debugging.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import FaceCluster, FaceEmbedding


router = APIRouter(prefix="/api/faces", tags=["faces"])


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
        "cluster_id": f.cluster_id,
        "source_plugin_id": f.source_plugin_id,
        "source_model_name": f.source_model_name,
        "confidence": f.confidence,
        "box": box,
        "frame_index": f.frame_index,
        "frame_t": f.frame_t,
    }


@router.get("/{face_id}")
def get_face(face_id: int, session: Session = Depends(get_db)):
    f = session.get(FaceEmbedding, face_id)
    if f is None:
        raise HTTPException(404, "face not found")
    return _face_dto(f)


@router.delete("/{face_id}")
def delete_face(face_id: int, session: Session = Depends(get_db)):
    f = session.get(FaceEmbedding, face_id)
    if f is None:
        raise HTTPException(404, "face not found")
    cluster_id = f.cluster_id
    session.delete(f)
    session.commit()
    if cluster_id is not None:
        # Re-query in a fresh transaction so the loader cache reflects the
        # now-deleted face; doing it before commit counts the still-
        # attached face and yields a stale face_count.
        cluster = session.get(FaceCluster, cluster_id)
        if cluster is not None:
            cluster.face_count = sum(1 for _ in cluster.faces)
            session.commit()
    return {"ok": True, "deleted": face_id}