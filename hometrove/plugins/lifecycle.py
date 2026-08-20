"""Plugin runtime lifecycle: status, startup/shutdown orchestration, logging.

Status is kept in-memory and reflects the current process. Log files are
shared across processes via append-mode writes.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from hometrove.plugins.log_sink import plugin_log, plugin_logs
from hometrove.plugins.registry import REGISTRY


class PluginStatus(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    ACTIVE = "active"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass
class PluginStatusInfo:
    status: PluginStatus = PluginStatus.IDLE
    detail: str = ""
    loaded_at: Optional[int] = None
    error_at: Optional[int] = None


@dataclass
class _LifecycleState:
    statuses: dict[str, PluginStatusInfo] = field(default_factory=dict)
    transitions: dict[str, threading.Lock] = field(default_factory=dict)
    global_lock: threading.Lock = field(default_factory=threading.Lock)


_STATE = _LifecycleState()


def _transition_lock(plugin_id: str) -> threading.Lock:
    with _STATE.global_lock:
        lock = _STATE.transitions.get(plugin_id)
        if lock is None:
            lock = threading.Lock()
            _STATE.transitions[plugin_id] = lock
        return lock


def set_status(plugin_id: str, status: PluginStatus, detail: str = "") -> None:
    with _STATE.global_lock:
        info = _STATE.statuses.setdefault(plugin_id, PluginStatusInfo())
        info.status = status
        info.detail = detail
        if status == PluginStatus.ACTIVE:
            info.loaded_at = int(time.time())
        if status == PluginStatus.ERROR:
            info.error_at = int(time.time())


def get_status(plugin_id: str) -> PluginStatusInfo:
    with _STATE.global_lock:
        info = _STATE.statuses.get(plugin_id)
        if info is None:
            return PluginStatusInfo()
        # Return a copy so callers cannot mutate internal state.
        return PluginStatusInfo(
            status=info.status,
            detail=info.detail,
            loaded_at=info.loaded_at,
            error_at=info.error_at,
        )


def is_active(plugin_id: str) -> bool:
    return get_status(plugin_id).status == PluginStatus.ACTIVE


def startup_plugin(plugin_id: str) -> None:
    plugin = REGISTRY.get(plugin_id)
    lock = _transition_lock(plugin_id)
    if not lock.acquire(blocking=False):
        return
    try:
        set_status(plugin_id, PluginStatus.LOADING)
        plugin_log(plugin_id, "INFO", "starting plugin")
        try:
            plugin.startup()
            set_status(plugin_id, PluginStatus.ACTIVE, "")
            plugin_log(plugin_id, "INFO", "plugin active")
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            set_status(plugin_id, PluginStatus.ERROR, detail)
            plugin_log(plugin_id, "ERROR", detail)
    finally:
        lock.release()


def shutdown_plugin(plugin_id: str) -> None:
    plugin = REGISTRY.get(plugin_id)
    lock = _transition_lock(plugin_id)
    if not lock.acquire(blocking=False):
        return
    try:
        set_status(plugin_id, PluginStatus.STOPPING)
        plugin_log(plugin_id, "INFO", "stopping plugin")
        try:
            plugin.shutdown()
            set_status(plugin_id, PluginStatus.IDLE, "")
            plugin_log(plugin_id, "INFO", "plugin stopped")
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            set_status(plugin_id, PluginStatus.ERROR, detail)
            plugin_log(plugin_id, "ERROR", detail)
    finally:
        lock.release()


def ensure_started(plugin_id: str) -> None:
    """Start a plugin if it is not already active/loading.

    Heavy-startup plugins (model loads/downloads) are started on a daemon
    thread so this call never blocks the caller — the plugin flips
    ``loading`` → ``active`` in the background. Light plugins start inline
    for predictable timing.
    """
    plugin = REGISTRY.get(plugin_id)
    if plugin is None:
        return
    info = get_status(plugin_id)
    if info.status in (PluginStatus.ACTIVE, PluginStatus.LOADING):
        return
    if getattr(plugin, "heavy_startup", False):
        threading.Thread(
            target=startup_plugin,
            args=(plugin_id,),
            name=f"startup-{plugin_id}",
            daemon=True,
        ).start()
    else:
        startup_plugin(plugin_id)


def start_all_enabled(enabled_ids: set[str]) -> None:
    """Start every enabled plugin that is not already active.

    Heavy plugins are started asynchronously (see ``ensure_started``) so a
    slow model load at boot does not hold up the API server.
    """

    for plugin_id in enabled_ids:
        if plugin_id not in {p.id for p in REGISTRY.list()}:
            continue
        ensure_started(plugin_id)


def get_logs(plugin_id: str, limit: int = 100) -> list[dict]:
    return [
        {"ts": e.ts, "level": e.level, "message": e.message}
        for e in plugin_logs(plugin_id, limit)
    ]
