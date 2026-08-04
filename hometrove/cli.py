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
        from hometrove.scanner import discover, enqueue_basic_info, upsert_assets
        from hometrove.config import get_settings

        settings = get_settings()
        roots = settings.media_roots_paths
        if not roots:
            print("no media roots configured (set HOMETROVE_MEDIA_ROOTS)", file=sys.stderr)
            return 2
        with session_scope() as s:
            discovered = list(discover(roots))
            new, skipped = upsert_assets(s, discovered)
            enq = enqueue_basic_info(s)
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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
