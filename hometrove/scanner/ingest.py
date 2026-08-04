"""Background ingest: take a finalized upload file and run scanner + plugin.

The upload manager hands us a single file path in ``staging/``. We need to
classify it, insert an ``assets`` row, and enqueue plugins — exactly what
scanner does, but for one file instead of a directory walk.
"""

from __future__ import annotations

import time
from pathlib import Path

from hometrove.models import Asset
from hometrove.plugins.builtin.basic_info import classify
from hometrove.scanner import hash_prefix
from hometrove.config import get_settings
from sqlalchemy.orm import Session


def ingest_file(session: Session, src: Path) -> int:
    """Create an asset for ``src`` (which lives in ``staging/``) and enqueue basic.info.

    Returns the new asset id, or ``-1`` if the file was lost mid-flight.
    """
    settings = get_settings()
    if not src.exists():
        return -1
    try:
        st = src.stat()
    except OSError:
        return -1
    media_type = classify(src).value
    try:
        h = hash_prefix(src, settings.hash_prefix_bytes)
    except OSError:
        h = ""

    # Use the full staging path as the asset path — these are owned by
    # HomeTrove, hence no extra media_root layer. The "staging/" prefix
    # makes the origin obvious to operators.
    key = f"uploads\0{src.resolve()}"
    existing = session.query(Asset).filter(Asset.path == key).one_or_none()
    if existing is not None:
        asset = existing
    else:
        now = int(time.time())
        asset = Asset(
            path=key,
            media_root=str(src.parent),
            content_hash=h,
            media_type=media_type,
            size_bytes=st.st_size,
            mtime=int(st.st_mtime),
            created_at=now,
            updated_at=now,
        )
        session.add(asset)
        session.commit()

    from hometrove.scanner import enqueue_pending
    enqueue_pending(session)
    return asset.id