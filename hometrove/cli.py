"""CLI entrypoint.

Usage::

    hometrove api      # start the FastAPI server
    hometrove worker   # start the indexing worker
    hometrove scan     # one-shot scan + enqueue
    hometrove migrate  # alembic upgrade head
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hometrove")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("api", help="start the FastAPI server")
    sub.add_parser("worker", help="start the indexing worker")
    sub.add_parser("serve", help="run API server and worker in one process")
    sub.add_parser("scan", help="scan media roots and enqueue plugins")
    sub.add_parser("migrate", help="run alembic upgrade head")

    # v1 trash: one-shot prune. The worker tick also runs this on its poll
    # loop when ``HOMETROVE_TRASH_AUTO_PURGE=true``; this CLI is for cron /
       # debugging / power users who'd rather not keep the worker running.
    trash_parser = sub.add_parser(
        "trash",
        help="trash management",
    )
    trash_sub = trash_parser.add_subparsers(dest="trash_cmd", required=True)
    prune = trash_sub.add_parser(
        "prune", help="drop trashed assets older than retention (default 30 days)",
    )
    prune.add_argument(
        "--older-than-days", type=int, default=None,
        help="override HOMETROVE_TRASH_RETENTION_DAYS for this run",
    )
    prune.add_argument(
        "--dry-run", action="store_true",
        help="count eligible rows without deleting",
    )
    empty = trash_sub.add_parser(
        "empty", help="permanently drop every trashed asset (no retention wait)",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "api":
        from hometrove.api import create_app
        import uvicorn

        app = create_app()
        uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
        return 0
    if args.cmd == "worker":
        from hometrove.worker.main import run_forever
        run_forever()
        return 0
    if args.cmd == "serve":
        # Run API server and worker in the same process: a Python-only
        # alternative to Docker for local / small deployments.
        import threading
        from hometrove.worker.main import run_forever
        from hometrove.api import create_app
        import uvicorn

        worker_thread = threading.Thread(target=run_forever, name="hometrove-worker", daemon=True)
        worker_thread.start()
        app = create_app()
        uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
        return 0
    if args.cmd == "scan":
        from hometrove.db import session_scope
        from hometrove.scanner import discover, enqueue_pending, upsert_assets
        from hometrove.config import get_settings
        from hometrove.readonly import run_for_settings

        settings = get_settings()
        roots = settings.media_roots_paths
        if not roots:
            print("no media roots configured (set HOMETROVE_MEDIA_ROOTS)", file=sys.stderr)
            return 2
        # M0-3: same per-root read-only warning the lifespan emits, so the
        # one-shot CLI exposes the same privacy signal without booting uvicorn.
        run_for_settings(settings)
        with session_scope() as s:
            discovered = list(discover(roots))
            new, skipped = upsert_assets(s, discovered)
            enq = enqueue_pending(s)
        print(f"new={new} skipped={skipped} enqueued={enq}")
        return 0
    if args.cmd == "migrate":
        from alembic.config import Config
        from alembic import command as alembic_command
        from hometrove.config import get_settings

        cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", get_settings().resolved_database_url())
        alembic_command.upgrade(cfg, "head")
        return 0
    if args.cmd == "trash":
        from hometrove.db import session_scope
        from hometrove.config import get_settings
        from hometrove.trash import empty_trash, purge_expired
        from sqlalchemy import select
        from hometrove.models import Asset

        if args.trash_cmd == "prune":
            settings = get_settings()
            days = args.older_than_days if args.older_than_days is not None else settings.trash_retention_days
            if days <= 0:
                print("refusing to prune with non-positive retention (set --older-than-days or HOMETROVE_TRASH_RETENTION_DAYS)", file=sys.stderr)
                return 2
            older_than = days * 86400
            with session_scope() as s:
                if args.dry_run:
                    cutoff = int(__import__("time").time()) - older_than
                    count = len(s.execute(
                        select(Asset.id).where(Asset.deleted_at.is_not(None), Asset.deleted_at < cutoff)
                    ).all())
                    print(f"dry-run: {count} asset(s) eligible for purge")
                else:
                    dropped = empty_trash(s, older_than_seconds=older_than)
                    print(f"dropped {dropped} asset(s) older than {days} day(s)")
            return 0
        if args.trash_cmd == "empty":
            with session_scope() as s:
                dropped = empty_trash(s, older_than_seconds=None)
            print(f"dropped {dropped} asset(s) (entire trash)")
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
