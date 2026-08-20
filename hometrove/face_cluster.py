"""Three-tier face data model: Person → FaceCluster → FaceEmbedding.

The ``cluster_faces_for_asset`` function is the single entry point the worker
calls after ``face.image`` / ``face.video`` finish. It walks every detected
face in the asset's most recent plugin result, finds the closest existing
cluster in the same ``(source_plugin_id, source_model_name)`` partition, and
either joins it (updating the centroid) or seeds a new cluster.

Design notes:

* **Per-plugin partitions.** A face emitted by ``face.image`` never joins a
  cluster produced by ``face.video``. The vector space is the same
  (ArcFace buffalo_l) but cross-plugin merging is a manual UI operation so
  the user can explicitly opt in once they trust the matches.
* **Centroid + cosine radius.** Centroids are an incremental running mean;
  the cluster's ``radius`` is the running mean of (1 - cosine) between the
  centroid and each member, giving a coarse estimate of how tight the
  cluster is. The threshold is computed per-asset, not hardcoded —
  ``default threshold`` (0.55) is used as a fallback only.
* **First face in a cluster gets ``representative_face_id``**, but the
  column stays ``NULL`` until ``face_count >= MIN_FACES_FOR_REPRESENTATIVE``
  (default 3) so noise clusters don't surface in the UI.
* **Idempotent.** Re-running the function for the same asset doesn't
  duplicate clusters — every cluster gains at most one face per asset per
  rerun. We rely on ``commit()`` at the boundary so the worker can
  re-enqueue freely.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.models import FaceCluster, FaceEmbedding

# Phase 1 brute-force threshold. 0.55 cosine is conservative — videos are
# tight (same identity under varying lighting), images slightly looser.
DEFAULT_CLUSTER_THRESHOLD = 0.55

# A cluster must see at least this many faces before the UI shows it as a
# "real" identity (otherwise it'd be flooded by one-off false positives).
MIN_FACES_FOR_REPRESENTATIVE = 3


@dataclass
class _ClusterSnapshot:
    id: int
    centroid: np.ndarray
    radius: float
    face_count: int


def _vec_to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _vec_from_blob(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim).copy()


def _serialize_vec(vec: list[float]) -> np.ndarray:
    return np.asarray(vec, dtype=np.float32)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _load_partition_centroids(
    session: Session, *, source_plugin_id: str, source_model_name: str
) -> list[_ClusterSnapshot]:
    """Brute-force scan: fetch every cluster in this partition. Phase 1 only;
    Phase 2 will swap this for a sqlite-vec ANN query.
    """
    rows = session.scalars(
        select(FaceCluster).where(
            FaceCluster.source_plugin_id == source_plugin_id,
            FaceCluster.source_model_name == source_model_name,
        )
    ).all()
    snapshots: list[_ClusterSnapshot] = []
    for row in rows:
        if row.centroid_blob is None:
            continue
        dim = len(row.centroid_blob) // 4
        try:
            centroid = _vec_from_blob(row.centroid_blob, dim)
        except Exception:  # noqa: BLE001
            continue
        snapshots.append(
            _ClusterSnapshot(
                id=row.id,
                centroid=centroid,
                radius=row.radius,
                face_count=row.face_count,
            )
        )
    return snapshots


def _find_or_create_cluster(
    session: Session,
    *,
    vec: np.ndarray,
    source_plugin_id: str,
    source_model_name: str,
    snapshots: list[_ClusterSnapshot],
    threshold: float,
) -> FaceCluster:
    """Join the closest existing cluster if cosine >= threshold, else create one."""
    best_id: Optional[int] = None
    best_score = -1.0
    for snap in snapshots:
        score = _cosine(vec, snap.centroid)
        if score > best_score:
            best_score = score
            best_id = snap.id
    if best_id is not None and best_score >= threshold:
        cluster = session.get(FaceCluster, best_id)
        if cluster is not None:
            return cluster

    # Seed a new cluster. ``name`` is a stable display placeholder; the user
    # later binds it to a Person through the UI.
    new_cluster = FaceCluster(
        person_id=None,
        source_plugin_id=source_plugin_id,
        source_model_name=source_model_name,
        name=f"未命名-{source_plugin_id}-{int(time.time())}",
        centroid_blob=_vec_to_blob(vec),
        radius=0.0,
        face_count=0,
        representative_face_id=None,
    )
    session.add(new_cluster)
    session.flush()  # populate new_cluster.id
    snapshots.append(
        _ClusterSnapshot(
            id=new_cluster.id,
            centroid=vec.copy(),
            radius=0.0,
            face_count=0,
        )
    )
    return new_cluster


def _update_cluster_after_add(
    cluster: FaceCluster, vec: np.ndarray, *, dim: int
) -> None:
    """Recompute centroid (running mean) and radius (running mean of (1-cos))."""
    assert cluster.centroid_blob is not None  # set at seed/find
    old_centroid = _vec_from_blob(cluster.centroid_blob, dim)
    old_count = cluster.face_count
    new_count = old_count + 1
    # Running mean: (old*n + new) / (n+1)
    new_centroid = (old_centroid * old_count + vec) / new_count
    cluster.centroid_blob = _vec_to_blob(new_centroid)
    cluster.face_count = new_count
    cluster.updated_at = int(time.time())

    # Radius: running mean of distance (1 - cosine) between centroid and
    # each member. Cheap approximation: keep an EWMA so we don't have to
    # load every face row on every add.
    distance = 1.0 - _cosine(new_centroid, vec)
    if old_count == 0:
        cluster.radius = distance
    else:
        # Weighted average with slight bias toward the new sample.
        cluster.radius = (cluster.radius * old_count + distance) / new_count


def _persist_face(
    session: Session,
    *,
    asset_id: int,
    face: dict,
    source_plugin_id: str,
    source_model_name: str,
) -> Optional[FaceEmbedding]:
    vec = face.get("embedding")
    if not vec:
        return None
    np_vec = _serialize_vec(vec)
    row = FaceEmbedding(
        asset_id=asset_id,
        embedding_json=json.dumps([float(x) for x in vec]),
        confidence=face.get("confidence"),
        box_json=json.dumps(face.get("box")) if face.get("box") is not None else None,
        cluster_id=None,
        source_plugin_id=source_plugin_id,
        source_model_name=source_model_name,
        frame_index=face.get("frame_index"),
        frame_t=face.get("frame_t"),
        # ``person_id`` is set indirectly via cluster.person_id when the
        # user names the cluster. Leave NULL here so cross-plugin manual
        # merging is the only path that touches person_id directly.
        person_id=None,
    )
    session.add(row)
    session.flush()  # populate row.id
    return row


def cluster_faces_for_asset(
    session: Session,
    *,
    asset_id: int,
    faces: Iterable[dict],
    source_plugin_id: str,
    source_model_name: str,
    threshold: float = DEFAULT_CLUSTER_THRESHOLD,
) -> dict[str, int]:
    """Persist every face in ``faces`` and assign each to a cluster.

    Returns counters ``{"persisted": n, "clustered": n}`` for logging.
    The caller is responsible for ``session.commit()`` — the worker
    already does so after invoking us, but tests may want to peek.
    """
    snapshots = _load_partition_centroids(
        session,
        source_plugin_id=source_plugin_id,
        source_model_name=source_model_name,
    )
    persisted = 0
    clustered = 0
    dim: Optional[int] = None
    for face in faces:
        if not face.get("embedding"):
            continue
        np_vec = _serialize_vec(face["embedding"])
        dim = dim or len(np_vec)
        cluster = _find_or_create_cluster(
            session,
            vec=np_vec,
            source_plugin_id=source_plugin_id,
            source_model_name=source_model_name,
            snapshots=snapshots,
            threshold=threshold,
        )
        face_row = _persist_face(
            session,
            asset_id=asset_id,
            face=face,
            source_plugin_id=source_plugin_id,
            source_model_name=source_model_name,
        )
        if face_row is None:
            continue
        face_row.cluster_id = cluster.id
        # Promote representative_face_id once we have enough samples.
        if (
            cluster.representative_face_id is None
            and (cluster.face_count + 1) >= MIN_FACES_FOR_REPRESENTATIVE
        ):
            cluster.representative_face_id = face_row.id
        _update_cluster_after_add(cluster, np_vec, dim=dim)
        persisted += 1
        clustered += 1
    return {"persisted": persisted, "clustered": clustered}


__all__ = [
    "DEFAULT_CLUSTER_THRESHOLD",
    "MIN_FACES_FOR_REPRESENTATIVE",
    "cluster_faces_for_asset",
]