from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.search import search

router = APIRouter(prefix="/api", tags=["search"])


@router.get("/search")
def do_search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(40, ge=1, le=100),
    session: Session = Depends(get_db),
):
    """Hybrid (vector + keyword) semantic search.

    Query syntax:
      * free text, e.g. ``sunset beach``
      * ``scope:scene sunset`` / ``scope:image sunset`` — restrict to video
        scene / image vectors (see the ``embeddings.scope`` column)
    """
    return search(session, q, limit=limit)
