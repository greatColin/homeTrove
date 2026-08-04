"""Face embedding matching and person grouping.

Design follows how photo apps (Synology Photos, Apple Photos) do it:

* A *detector* (e.g. ``mock.faces``) emits only vectors — never names.
* A *matcher* compares each vector against the library's known embeddings
  using cosine similarity. Above a threshold it is grouped under the best
  matching person; otherwise a new ``未命名-<rand>`` person is created.
* Naming a person triggers a *backfill sweep*: that person's current vectors
  are compared against every embedding currently grouped under a *different*
  (unnamed) person, and close matches are moved over. This mirrors the
  "name once, the app pulls in the rest of that person's old photos" flow.
* Merging two persons is a manual, explicit operation (never automatic).

All vectors are stored as JSON arrays of floats; cosine similarity is
computed in plain Python (M0 — an ANN index is the M1 upgrade path).
"""

from __future__ import annotations

import json
import math
import random
import time
from typing import Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from hometrove.models import FaceEmbedding, Person


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load_embedding(row: FaceEmbedding) -> list[float]:
    try:
        return json.loads(row.embedding_json)
    except (json.JSONDecodeError, TypeError):
        return []


def _new_unnamed_name() -> str:
    # 未命名 + random number, opaque until the user names it.
    return f"未命名-{random.randint(1000, 9999)}"


def _best_person(session: Session, vec: list[float], threshold: float) -> Optional[Person]:
    """Return the person whose embeddings are most similar to ``vec`` (if any
    reaches the threshold)."""
    rows = session.execute(select(FaceEmbedding)).scalars().all()
    best_person: Optional[Person] = None
    best_sim = threshold
    for row in rows:
        sim = cosine_similarity(vec, _load_embedding(row))
        if sim > best_sim:
            best_sim = sim
            best_person = row.person
    return best_person


def match_face(
    session: Session,
    asset_id: int,
    embedding: list[float],
    confidence: Optional[float],
    box: Optional[list[int]],
    *,
    threshold: float = 0.75,
) -> FaceEmbedding:
    """Group one detected face under the best-matching person, creating a new
    unnamed person when nothing matches."""
    person = _best_person(session, embedding, threshold)
    if person is None:
        person = Person(name=_new_unnamed_name())
        session.add(person)
        session.flush()  # get person.id
    row = FaceEmbedding(
        person_id=person.id,
        asset_id=asset_id,
        embedding_json=json.dumps(embedding),
        confidence=confidence,
        box_json=json.dumps(box) if box is not None else None,
    )
    session.add(row)
    session.flush()
    return row


def match_asset_faces(
    session: Session,
    asset_id: int,
    faces: Iterable[dict],
    *,
    threshold: float = 0.75,
) -> int:
    """Group every detected face in one asset. Returns number of faces."""
    count = 0
    for f in faces:
        vec = f.get("embedding")
        if not vec:
            continue
        match_face(
            session,
            asset_id,
            vec,
            f.get("confidence"),
            f.get("box"),
            threshold=threshold,
        )
        count += 1
    session.commit()
    return count


def name_person_and_backfill(session: Session, person: Person, new_name: str) -> int:
    """Set a display name, then sweep the library for similar unnamed faces.

    Mirrors the photo-app behavior: after you name a person, previously
    indexed photos of the same person get pulled into that person's set.
    Returns the number of faces re-assigned.
    """
    if person.name == new_name and not new_name.startswith("未命名"):
        # Already named; still allow re-backfill on explicit save.
        pass
    person.name = new_name
    person.updated_at = int(time.time())

    # Representative vector: average of this person's current embeddings.
    mine = [r for r in person.faces]
    if not mine:
        session.commit()
        return 0
    dim = len(_load_embedding(mine[0]))
    avg = [0.0] * dim
    for r in mine:
        v = _load_embedding(r)
        for i in range(min(dim, len(v))):
            avg[i] += v[i]
    avg = [x / len(mine) for x in avg]

    # Look for unnamed persons whose faces are close to the representative.
    unnamed = session.execute(
        select(Person).where(Person.name.like("未命名%"))
    ).scalars().all()
    moved = 0
    for other in unnamed:
        if other.id == person.id:
            continue
        move_ids = [
            face.id
            for face in other.faces
            if cosine_similarity(avg, _load_embedding(face)) >= 0.75
        ]
        if move_ids:
            session.execute(
                update(FaceEmbedding)
                .where(FaceEmbedding.id.in_(move_ids))
                .values(person_id=person.id)
            )
            moved += len(move_ids)
    # Persist the rename and any moves before refreshing state; expire_all()
    # would otherwise discard the not-yet-flushed ``person.name`` change.
    session.flush()
    session.expire_all()
    # Re-check for unnamed persons left without faces after the move.
    for other in session.execute(
        select(Person).where(Person.name.like("未命名%"))
    ).scalars().all():
        if other.id != person.id and not other.faces:
            session.delete(other)
    session.commit()
    return moved


def merge_persons(session: Session, keep_id: int, remove_id: int) -> int:
    """Move every face of ``remove_id`` under ``keep_id`` and delete the
    orphaned person. Returns number of faces moved."""
    keep = session.get(Person, keep_id)
    remove = session.get(Person, remove_id)
    if keep is None or remove is None:
        raise ValueError("person not found")
    if keep_id == remove_id:
        raise ValueError("cannot merge a person into itself")
    # Move rows at the SQL level. Mutating the relationship collection would
    # make SQLAlchemy treat each re-assigned face as a delete-orphan.
    moved = session.execute(
        update(FaceEmbedding)
        .where(FaceEmbedding.person_id == remove_id)
        .values(person_id=keep_id)
    ).rowcount
    # Refresh state so the remove's loaded faces no longer reference it, then
    # delete the (now empty) person without delete-orphan side effects.
    session.expire_all()
    remove = session.get(Person, remove_id)
    session.delete(remove)
    session.commit()
    return int(moved or 0)
