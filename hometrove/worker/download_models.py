"""Background model-download worker.

InsightFace packs (e.g. ``buffalo_l``) are not pip-installable and live
under ``~/.insightface/models/<pack>``. The download button in the UI
calls ``POST /api/plugins/{id}/download-model``, which schedules a
background thread that drives ``FaceAnalysis.prepare()`` (which in turn
performs InsightFace's own CDN download).

The HTTP request returns immediately with ``started=True``; the UI polls
``/api/plugins/{id}/status`` to watch the plugin flip from ``error`` →
``active``.

The worker is process-local: threads share the same ``InsightFaceRuntime``
singleton, so a single download attempt per pack is sufficient even if
the button is clicked twice. A per-plugin lock prevents concurrent
downloads of the same pack.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from hometrove.insightface_runtime import (
    ensure_downloaded,
    reset_for_test,
)
from hometrove.plugins.lifecycle import (
    PluginStatus,
    get_status,
    plugin_log,
    set_status,
    shutdown_plugin,
    startup_plugin,
)

_log = logging.getLogger("hometrove.worker.download_models")

_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _lock_for(plugin_id: str) -> threading.Lock:
    with _GUARD:
        lock = _LOCKS.get(plugin_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[plugin_id] = lock
        return lock


def _run(plugin_id: str, model_name: str, lock: threading.Lock) -> None:
    """Thread entry point. Sets status to ``loading`` and then ``active``
    on success or ``error`` on failure. Releases the per-plugin lock when
    done so a second click can re-trigger.
    """
    try:
        set_status(plugin_id, PluginStatus.LOADING, "downloading model...")
        plugin_log(plugin_id, "INFO", f"starting model download for {model_name}")
        t0 = time.monotonic()
        try:
            ok = ensure_downloaded(model_name)
        except Exception as exc:  # noqa: BLE001
            detail = f"download raised: {type(exc).__name__}: {exc}"
            set_status(plugin_id, PluginStatus.ERROR, detail)
            plugin_log(plugin_id, "ERROR", detail)
            return
        if not ok:
            detail = (
                "model download failed; check InsightFace CDN reachability. "
                "Use the installer's diagnostic script for retry."
            )
            set_status(plugin_id, PluginStatus.ERROR, detail)
            plugin_log(plugin_id, "ERROR", detail)
            return

        # Drop the in-process singleton so the next startup_plugin() pulls
        # the freshly-downloaded pack into memory.
        reset_for_test()

        # Restart the plugin so face.image / face.video share a fresh
        # ``FaceAnalysis`` instance over the new pack. shutdown_plugin is
        # idempotent — if the plugin was never started it returns quietly.
        shutdown_plugin(plugin_id)
        startup_plugin(plugin_id)

        elapsed = int(time.monotonic() - t0)
        set_status(
            plugin_id,
            PluginStatus.ACTIVE,
            f"model {model_name} downloaded + plugin restarted ({elapsed}s)",
        )
        plugin_log(
            plugin_id,
            "INFO",
            f"model {model_name} downloaded in {elapsed}s; plugin active",
        )
    finally:
        lock.release()


def start_model_download(plugin_id: str, model_name: str) -> bool:
    """Schedule a background download. Returns ``False`` if one is already
    in flight for this plugin (UI button is idempotent).
    """
    lock = _lock_for(plugin_id)
    if not lock.acquire(blocking=False):
        return False
    thread = threading.Thread(
        target=_run,
        args=(plugin_id, model_name, lock),
        name=f"download-{plugin_id}",
        daemon=True,
    )
    thread.start()
    _REGISTRY[plugin_id] = (thread, lock)
    return True


_REGISTRY: dict[str, tuple[threading.Thread, threading.Lock]] = {}


def get_active_download(plugin_id: str) -> Optional[str]:
    """Diagnostic helper: name of the thread currently downloading for a
    plugin, or None.
    """
    entry = _REGISTRY.get(plugin_id)
    if entry is None:
        return None
    thread, _ = entry
    if thread.is_alive():
        return thread.name
    return None