"""Long-running worker process.

Polls ``jobs`` for ``pending`` rows in dependency order (DAG), runs each
plugin via the orchestrator's runner, then loops. Idempotent against
crashes: a job stuck in ``running`` is reported as zombie after a deadline
and re-queued.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time
from pathlib import Path

from sqlalchemy import select

from hometrove.config import get_settings
from hometrove.events import BUS, JobEvent
from hometrove.orchestrator.dag import Node, build_graph
from hometrove.orchestrator.runner import run_one
from hometrove.db import session_scope
from hometrove.models import Job, PluginResult
from hometrove.plugins.registry import REGISTRY
import hometrove.plugins.builtin  # noqa: F401  ensure built-in plugins are registered


log = logging.getLogger("hometrove.worker")


def _build_dag():
    nodes = [
        Node(plugin_id=p.id, depends_on=list(p.depends_on))
        for p in REGISTRY.list()
    ]
    return build_graph(nodes)


def _claim_next(session, my_claim_token: str) -> Job | None:
    """Atomically pick the oldest ``pending`` job whose DAG dependencies are
    satisfied (the job's plugin has a ``done`` result for that asset), and mark
    it ``running``."""
    plugins = {p.id: p for p in REGISTRY.list()}
    jobs = session.execute(
        select(Job).where(Job.state == "pending").order_by(Job.enqueued_at)
    ).scalars().all()
    for j in jobs:
        plugin = plugins.get(j.plugin_id)
        deps = plugin.depends_on if plugin is not None else []
        ready = True
        for dep in deps:
            pr = session.get(
                PluginResult, (j.asset_id, dep, plugins[dep].version)
            )
            if pr is None or pr.status != "ok":
                ready = False
                break
        if not ready:
            continue
        j.state = "running"
        j.started_at = int(time.time())
        j.error = None
        session.commit()
        return j
    return None


async def _publish(event_type: str, payload: dict) -> None:
    try:
        await BUS.publish(JobEvent(event_type, time.time(), payload))
    except Exception:  # noqa: BLE001
        log.exception("failed to publish event")


async def main_async(stop: asyncio.Event) -> None:
    settings = get_settings()
    poll = settings.worker_poll_interval_seconds
    log.info("worker main_async started (poll=%.2fs)", poll)
    while not stop.is_set():
        try:
            with session_scope() as s:
                job = _claim_next(s, "")
            if job is None:
                # idle
                await asyncio.sleep(poll)
                continue
            # Run synchronously in a thread so plugins can use blocking IO.
            log.info("claim job %s plugin=%s -> running", job.id, job.plugin_id)
            await _publish("job-update", {"id": job.id, "state": "running"})
            try:
                log.info("dispatching job %s to thread executor", job.id)
                await asyncio.to_thread(run_one, job.id, _new_session())
                log.info("job %s finished in thread", job.id)
                with session_scope() as s:
                    fresh = s.get(Job, job.id)
                    state = fresh.state if fresh else "unknown"
                await _publish("job-update", {"id": job.id, "state": state})
            except Exception as exc:  # noqa: BLE001
                log.exception("job %s crashed", job.id)
                await _publish("job-update", {"id": job.id, "state": "failed", "error": str(exc)})
        except Exception as e:  # noqa: BLE001
            log.exception("worker loop error: %s", e)
            await asyncio.sleep(min(2.0, poll * 2))


def _new_session():
    from hometrove.db import session_factory
    return session_factory()()


def run_forever() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(*_):
        stop.set()

    # Signal handlers only work in the main thread; when running inside a
    # worker thread (e.g. ``hometrove serve``) they must be skipped.
    if threading.current_thread() is threading.main_thread():
        for sig_name in ("SIGINT", "SIGTERM"):
            try:
                loop.add_signal_handler(getattr(signal, sig_name), _shutdown)
            except NotImplementedError:
                pass

    try:
        loop.run_until_complete(main_async(stop))
    finally:
        loop.close()
