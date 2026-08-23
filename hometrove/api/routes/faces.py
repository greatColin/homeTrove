"""Face management API.

Faces are emitted by ``face.image`` / ``face.video`` and persisted by the
auto-cluster pipeline. Most user actions happen at the cluster level;
this router exposes single-face read and delete for the UI's "remove this
false positive" affordance and for debugging.

Also exposes ``POST /api/faces/recognize`` which accepts an uploaded image and
returns the most-similar persons from the library, enabling the "search by
photo" use-case.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.faces import cosine_similarity as _cosine_sim
from hometrove.insightface_runtime import ModelMissingError, acquire, release
from hometrove.models import FaceCluster, FaceEmbedding, Person


router = APIRouter(prefix="/api/faces", tags=["faces"])


def _load_embedding(row: FaceEmbedding) -> list[float]:
    try:
        return json.loads(row.embedding_json)
    except (json.JSONDecodeError, TypeError):
        return []


def _best_person_matches(
    session: Session,
    query_vec: list[float],
    top_k: int = 5,
    score_threshold: float = 0.3,
) -> list[dict]:
    """Find the top-K persons/clusters most similar to query_vec.

    For assigned persons (person_id is not None): groups all their embeddings,
    returns avg similarity + rep face.
    For unassigned faces (person_id is None): groups by cluster_id, returns
    max similarity within the cluster + cluster's rep face.
    """
    rows = session.execute(select(FaceEmbedding)).scalars().all()

    # assigned: person_id -> list of scores
    assigned: dict[int, list[float]] = {}
    # unassigned: cluster_id -> list of scores
    unassigned: dict[int, list[float]] = {}

    for row in rows:
        vec = _load_embedding(row)
        if not vec:
            continue
        sim = _cosine_sim(query_vec, vec)
        if sim < score_threshold:
            continue
        if row.person_id is not None:
            assigned.setdefault(row.person_id, []).append(sim)
        elif row.cluster_id is not None:
            unassigned.setdefault(row.cluster_id, []).append(sim)

    results: list[tuple[float, int | None, str, int, int | None, int | None]] = []

    # Assigned persons
    for person_id, scores in assigned.items():
        person = session.get(Person, person_id)
        if person is None:
            continue
        avg = sum(scores) / len(scores)
        rep_asset_id = None
        for c in person.clusters:
            if c.representative_face_id:
                rep_face = session.get(FaceEmbedding, c.representative_face_id)
                if rep_face:
                    rep_asset_id = rep_face.asset_id
                    break
        results.append((avg, person_id, person.name, len(scores), rep_asset_id, None))

    # Unassigned clusters
    for cluster_id, scores in unassigned.items():
        cluster = session.get(FaceCluster, cluster_id)
        if cluster is None:
            continue
        best_sim = max(scores)
        rep_asset_id = None
        if cluster.representative_face_id:
            rep_face = session.get(FaceEmbedding, cluster.representative_face_id)
            if rep_face:
                rep_asset_id = rep_face.asset_id
        results.append((best_sim, None, cluster.name, len(scores), rep_asset_id, cluster_id))

    results.sort(key=lambda x: x[0], reverse=True)
    results = results[:top_k]

    out = []
    for avg, pid, name, cnt, rep, cid in results:
        out.append(
            {
                "person_id": pid,
                "name": name,
                "avg_score": round(avg, 4),
                "matched_count": cnt,
                "representative_face_asset_id": rep,
                "cluster_id": cid,
            }
        )
    return out


@router.post("/recognize")
def recognize_face(
    file: UploadFile = File(...),
    top_k: int = 5,
    score_threshold: float = 0.3,
    session: Session = Depends(get_db),
):
    """Accept an image, detect faces with InsightFace, and return the most-
    similar persons already in the library.

    Each detected face is matched independently; the response includes all
    matches across all faces so the client can surface the best candidates.
    """
    try:
        app = acquire("buffalo_l")
    except ModelMissingError:
        raise HTTPException(
            503,
            "模型未下载。请到「插件设置 → 状态」页面下载 buffalo_l 模型后再试。",
        )

    try:
        import numpy as np
        contents = file.file.read()
        pil_img = Image.open(file.file if hasattr(file.file, "seek") else __import__("io").BytesIO(contents))
        if pil_img.mode != "RGB":
            pil_img = pil_img.convert("RGB")
        img_array = np.asarray(pil_img)
    except Exception as exc:
        raise HTTPException(400, f"无法解码图片：{exc}")

    try:
        faces = app.get(img_array, max_num=0)
    except Exception as exc:
        raise HTTPException(400, f"人脸检测失败：{exc}")
    finally:
        release()

    if not faces:
        return {"faces": [], "matches": []}

    all_matches = []
    for face in faces:
        vec = [round(float(x), 6) for x in face.embedding.tolist()]
        det_score = round(float(face.det_score), 4)
        bbox = [int(x) for x in face.bbox.tolist()]
        person_matches = _best_person_matches(session, vec, top_k=top_k, score_threshold=score_threshold)
        all_matches.append(
            {
                "det_score": det_score,
                "bbox": bbox,
                "matches": person_matches,
            }
        )

    return {"faces": all_matches, "total_detected": len(faces)}


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
        cluster = session.get(FaceCluster, cluster_id)
        if cluster is not None:
            cluster.face_count = sum(1 for _ in cluster.faces)
            session.commit()
    return {"ok": True, "deleted": face_id}


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
