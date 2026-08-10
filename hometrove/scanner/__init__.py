"""Media scanner.

M0: walks every media root, deduplicates by ``content_hash_prefix`` (cheap),
inserts new ``assets`` rows, and enqueues a ``basic.info`` job per asset.

The scanner is intentionally synchronous and streamed — large libraries
are handled by yielding one asset at a time and committing at batch
boundaries.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from hometrove.config import get_settings
from hometrove.models import Asset, Job
from hometrove.plugins.builtin.basic_info import classify


@dataclass
class DiscoveredAsset:
    root: Path
    absolute_path: Path
    rel_path: str
    media_type: str
    size_bytes: int
    mtime: int
    content_hash_prefix: str


def hash_prefix(path: Path, n: int) -> str:
    """SHA-256 of the first ``n`` bytes of the file.

    This is what M0 dedupes on. A small ``n`` keeps the scanner fast on
    huge libraries, at the cost of occasional collisions — acceptable for
    M0 and explicitly noted in docs.
    """
    h = hashlib.sha256()
    remaining = n
    with open(path, "rb") as f:
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def iter_paths(root: Path) -> Iterator[Path]:
    """Yield every file under ``root`` recursively. Symlinks not followed."""
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            yield Path(dirpath) / name


def discover(roots: Iterable[Path], *, max_files: Optional[int] = None) -> Iterator[DiscoveredAsset]:
    """Walk each root and yield DiscoveredAsset records."""
    settings = get_settings()
    seen = 0
    for root in roots:
        root = root.resolve()
        if not root.exists():
            continue
        for path in iter_paths(root):
            try:
                st = path.stat()
            except OSError:
                continue
            mt = classify(path).value
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = path.name
            try:
                prefix = hash_prefix(path, settings.hash_prefix_bytes)
            except OSError:
                prefix = ""
            yield DiscoveredAsset(
                root=root,
                absolute_path=path,
                rel_path=rel,
                media_type=mt,
                size_bytes=st.st_size,
                mtime=int(st.st_mtime),
                content_hash_prefix=prefix,
            )
            seen += 1
            if max_files is not None and seen >= max_files:
                return


def upsert_assets(
    session: Session,
    discovered: Iterable[DiscoveredAsset],
    *,
    commit_batch: int = 200,
) -> Tuple[int, int]:
    """Insert new assets, update existing ones, return ``(new, skipped)`` counts."""
    now = int(time.time())
    new = 0
    skipped = 0
    pending = 0
    for d in discovered:
        # ``\0`` is illegal in Linux filesystem paths so it's a safe in-database
        # delimiter to combine root + rel. We *never* pass ``key`` to any
        # filesystem API directly — plugins reconstruct paths by splitting.
        key = f"{d.root}\0{d.rel_path}"
        filename = d.rel_path.split("/")[-1] or d.rel_path
        existing = session.execute(
            select(Asset).where(Asset.path == key)
        ).scalar_one_or_none()
        if existing is not None:
            existing.content_hash = d.content_hash_prefix
            existing.media_type = d.media_type
            existing.size_bytes = d.size_bytes
            existing.mtime = d.mtime
            existing.filename = filename
            existing.updated_at = now
            skipped += 1
        else:
            session.add(
                Asset(
                    path=key,
                    filename=filename,
                    media_root=str(d.root),
                    content_hash=d.content_hash_prefix,
                    media_type=d.media_type,
                    size_bytes=d.size_bytes,
                    mtime=d.mtime,
                    created_at=now,
                    updated_at=now,
                )
            )
            new += 1

        pending += 1
        if pending >= commit_batch:
            session.commit()
            pending = 0

    if pending:
        session.commit()

    return new, skipped


def enqueue_basic_info(session: Session) -> int:
    """Enqueue ``basic.info`` for assets that do not yet have a successful job.

    Idempotent: re-running on a steady-state library is a no-op.
    """
    return enqueue_pending(session, plugin_ids=["basic.info"])


def enqueue_pending(
    session: Session,
    plugin_ids: list[str] | None = None,
    asset_ids: list[int] | None = None,
    media_types: list[str] | None = None,
) -> int:
    """Enqueue every *enabled* plugin for assets missing a successful result.

    Idempotent per (asset, plugin): a plugin whose result already exists with
    status ``ok`` is skipped, and a live (pending/running) job is not
    duplicated. Plugins disabled via ``plugin_config.enabled=0`` are skipped
    entirely — their jobs are neither created nor requeued here.

    ``asset_ids`` restricts the enqueue to a specific set of assets.
    ``media_types`` filters assets by their media type; useful when a caller
    wants to respect a plugin's supported media set.
    """
    from hometrove.models import PluginConfig
    from hometrove.plugins.api import AssetLike
    from hometrove.plugins.registry import REGISTRY

    if plugin_ids is None:
        plugins = REGISTRY.list()
    else:
        plugins = [REGISTRY.get(pid) for pid in plugin_ids]

    # Absence from plugin_config means "enabled by default" (the lifespan /
    # bootstrap seed rows on startup). Only plugins explicitly disabled
    # (enabled=0) are filtered out.
    disabled_ids = set(
        session.scalars(
            select(PluginConfig.plugin_id).where(PluginConfig.enabled == 0)
        ).all()
    )
    plugins = [p for p in plugins if p.id not in disabled_ids]

    now = int(time.time())
    enqueued = 0

    for plugin in plugins:
        try:
            est_cost = float(plugin.estimate(AssetLike()).seconds)
        except Exception:  # noqa: BLE001
            est_cost = 0.02

        stmt = select(Asset.id).where(
            ~exists().where(
                (Job.asset_id == Asset.id)
                & (Job.plugin_id == plugin.id)
                & (Job.state == "done")
            )
        )
        if asset_ids is not None:
            stmt = stmt.where(Asset.id.in_(asset_ids))
        if media_types is not None:
            stmt = stmt.where(Asset.media_type.in_(media_types))

        ids = session.execute(stmt).scalars().all()

        for asset_id in ids:
            live = session.execute(
                select(Job.id).where(
                    Job.asset_id == asset_id,
                    Job.plugin_id == plugin.id,
                    Job.state.in_(["pending", "running"]),
                )
            ).first()
            if live is not None:
                continue
            session.add(
                Job(
                    asset_id=asset_id,
                    plugin_id=plugin.id,
                    state="pending",
                    est_cost=est_cost,
                    enqueued_at=now,
                )
            )
            enqueued += 1
    session.commit()
    return enqueued
