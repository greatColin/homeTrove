"""Persistent, rotating log sink for individual plugins.

Each plugin gets its own log file under ``{data_dir}/plugin-logs/``. Logs are
written as newline-delimited JSON so multiple processes (API server / worker)
can append safely. Files are rotated by size to bound disk usage.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PluginLogEntry:
    ts: int
    level: str
    message: str

    def to_json(self) -> str:
        return json.dumps({"ts": self.ts, "level": self.level, "message": self.message}, ensure_ascii=False)


class PluginLogSink:
    """Per-plugin rotating log file.

    Thread-safe within a process. Multi-process safety relies on append-mode
    file writes (POSIX O_APPEND). Rotation is best-effort when concurrent
    processes race; the worst case is one extra backup file.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        max_bytes: int = 1 * 1024 * 1024,
        backup_count: int = 3,
    ) -> None:
        self._base_dir = Path(data_dir) / "plugin-logs"
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max(max_bytes, 1024)
        self._backup_count = max(backup_count, 0)
        self._locks: dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _path(self, plugin_id: str) -> Path:
        # plugin ids may contain dots; keep them in the filename.
        safe = plugin_id.replace("/", "_").replace("\\", "_")
        return self._base_dir / f"{safe}.log"

    def _lock(self, plugin_id: str) -> threading.Lock:
        with self._global_lock:
            lock = self._locks.get(plugin_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[plugin_id] = lock
            return lock

    def _rotate(self, path: Path) -> None:
        if not path.exists():
            return
        # Rotate backups: .log.3 -> .log.4, .log.2 -> .log.3, ...
        for i in range(self._backup_count, 0, -1):
            src = path.parent / f"{path.stem}.log.{i}"
            dst = path.parent / f"{path.stem}.log.{i + 1}"
            if src.exists():
                src.replace(dst)
        dst = path.parent / f"{path.stem}.log.1"
        path.replace(dst)

    def log(self, plugin_id: str, level: str, message: str) -> None:
        entry = PluginLogEntry(ts=int(time.time()), level=level.upper(), message=message)
        path = self._path(plugin_id)
        with self._lock(plugin_id):
            try:
                if path.exists() and path.stat().st_size >= self._max_bytes:
                    self._rotate(path)
            except OSError:
                pass
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(entry.to_json() + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                pass

    def get_logs(self, plugin_id: str, limit: int = 100) -> list[PluginLogEntry]:
        path = self._path(plugin_id)
        out: deque[PluginLogEntry] = deque(maxlen=limit)
        if not path.is_file():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        out.append(
                            PluginLogEntry(
                                ts=int(obj.get("ts", 0)),
                                level=str(obj.get("level", "INFO")),
                                message=str(obj.get("message", "")),
                            )
                        )
                    except (json.JSONDecodeError, ValueError):
                        continue
        except OSError:
            pass
        return list(out)


# Global singleton; initialized lazily from settings.
_SINK: Optional[PluginLogSink] = None
_INIT_LOCK = threading.Lock()


def get_sink(data_dir: Optional[Path] = None) -> PluginLogSink:
    global _SINK
    if _SINK is not None:
        return _SINK
    with _INIT_LOCK:
        if _SINK is not None:
            return _SINK
        if data_dir is None:
            from hometrove.config import get_settings

            data_dir = get_settings().resolved_data_dir()
        _SINK = PluginLogSink(data_dir)
    return _SINK


def reset_sink() -> None:
    global _SINK
    with _INIT_LOCK:
        _SINK = None


def plugin_log(plugin_id: str, level: str, message: str) -> None:
    get_sink().log(plugin_id, level, message)


def plugin_logs(plugin_id: str, limit: int = 100) -> list[PluginLogEntry]:
    return get_sink().get_logs(plugin_id, limit)
