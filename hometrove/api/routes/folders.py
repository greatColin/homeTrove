"""Folders tree API.

M0 is pragmatic: it returns the union of distinct ``media_root`` parent
directories and the original directory layout under them, derived from
``asset.path`` (which we store as ``root|rel_path`` for root-mounted
content). For uploaded files, we synthesize a single synthetic "uploads"
folder per day to keep the layout introspectable.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import Asset


router = APIRouter(prefix="/api/folders", tags=["folders"])


def _split_path(path: str) -> tuple[str, list[str]]:
    """Split a stored asset path into ``(media_root_str, segments)``.

    Stored paths use a literal ``\\0`` between ``media_root`` and the relative
    path. We surface the highest-level directory name under the root so the
    UI can group by top-level folder.
    """
    if "\0" in path:
        root, rest = path.split("\0", 1)
        segments = [seg for seg in rest.split("/") if seg]
        return root, segments
    # Uploaded file: surface under a virtual "uploads/<date>" root so the UI
    # has somewhere to anchor it.
    p = PurePosixPath(path)
    return "uploads", list(p.parent.parts) + [p.name] if p.parent != p else [p.name]


@router.get("")
def list_folders(session: Session = Depends(get_db)):
    """Return a list of top-level folders (one per media_root) with counts."""
    counts: dict[tuple[str, ...], int] = defaultdict(int)
    media_types: dict[tuple[str, ...], dict[str, int]] = defaultdict(lambda: {"image": 0, "video": 0, "other": 0})

    rows = session.execute(
        select(Asset.media_root, Asset.media_type).where(Asset.deleted_at.is_(None))
    ).all()
    for root, media_type in rows:
        # Bucket: the entire media root counts as one folder for M0 simplicity.
        # Sub-folder navigation lands in M1.
        key = (root,)
        counts[key] += 1
        media_types[key][media_type] += 1

    out = []
    for (root,) in sorted(counts.keys()):
        out.append(
            {
                "media_root": root,
                "total": counts[(root,)],
                "media_types": dict(media_types[(root,)]),
            }
        )
    return {"roots": out}


@router.get("/assets")
def assets_in_folder(media_root: str, session: Session = Depends(get_db)):
    """List assets under a specific media root."""
    rows = (
        session.execute(
            select(Asset)
            .where(Asset.media_root == media_root, Asset.deleted_at.is_(None))
            .order_by(Asset.id)
            .limit(1000)
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": a.id,
                "path": a.path.split("\0", 1)[-1] if "\0" in a.path else a.path,
                "media_type": a.media_type,
                "size_bytes": a.size_bytes,
                "mtime": a.mtime,
                "width": a.width,
                "height": a.height,
            }
            for a in rows
        ],
    }
