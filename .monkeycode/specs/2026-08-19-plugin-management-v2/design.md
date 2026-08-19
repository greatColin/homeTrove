# Plugin Management v2

Feature Name: plugin-management-v2
Updated: 2026-08-19

## Description

Add an explicit runtime lifecycle for plugins (`idle` → `loading` → `active` / `error`), expose per-plugin logs, restrict the upload plugin selector to enabled plugins with a "select all" control, and provide matching CLI commands.

## Architecture

```mermaid
graph TD
    A[Web UI: /settings/plugins] -->|GET /api/plugins| B[Plugin Registry]
    A -->|PUT /api/plugins/{id}| C[Plugin Lifecycle Manager]
    A -->|GET /api/plugins/{id}/status| D[In-Memory Status Store]
    A -->|GET /api/plugins/{id}/logs| E[In-Memory Log Buffer]
    F[Web UI: /upload] -->|GET /api/plugins?enabled=true| B
    G[hometrove-cli] -->|plugins --enabled| B
    G -->|describe-plugin {id}| B
    C -->|startup / shutdown| H[BasePlugin subclasses]
```

## Components and Interfaces

### 1. BasePlugin lifecycle hooks

Extend `hometrove/plugins/base.py`:

```python
class BasePlugin:
    ...
    def startup(self) -> None:
        """Initialize process-level resources (models, pools, etc.)."""

    def shutdown(self) -> None:
        """Release process-level resources."""
```

Default implementations are no-ops. Heavy plugins (`face.detect`, `vlm.qwen3vl`, etc.) can override `startup()` to load models lazily when enabled.

### 2. Plugin lifecycle manager

New module `hometrove/plugins/lifecycle.py`:

- `_STATUSES: dict[str, PluginStatusInfo]` — in-memory runtime status per plugin id.
- `set_status(plugin_id, status, detail="")` — thread-safe status update.
- `get_status(plugin_id) -> PluginStatusInfo`.
- `startup_plugin(plugin_id)` / `shutdown_plugin(plugin_id)` — run hooks and update status.

### 3. Plugin log file sink

New module `hometrove/plugins/log_sink.py`:

- Logs are written to `{data_dir}/plugin-logs/{plugin_id}.log`.
- A `RotatingFileHandler`-style rotation keeps at most `N` backup files (default 3) with a max size per file (default 1 MB).
- `log(plugin_id, level, message)` appends a JSON line: `{"ts": 123456, "level": "INFO", "message": "..."}`.
- `get_logs(plugin_id, limit=100) -> list[PluginLogEntry]` reads the file tail efficiently.
- API and Worker processes share the same log files via append mode.

### 3. Backend API changes

`hometrove/api/routes/plugins.py`:

- `GET /api/plugins` accepts `?enabled=true` to return only enabled plugins.
- `PUT /api/plugins/{plugin_id}` calls `startup_plugin()` when enabling and `shutdown_plugin()` when disabling, then returns the updated DTO including `status`.
- `GET /api/plugins/{plugin_id}/status` returns `{"status": "active", "detail": "", "loaded_at": 123456}`.
- `GET /api/plugins/{plugin_id}/logs?limit=100` returns `{"items": [{"ts", "level", "message"}]}`.

DTO additions:

```json
{
  "id": "face.detect",
  "name": "人脸检测",
  "status": "active",
  "status_detail": "",
  "enabled": true,
  ...
}
```

### 4. Frontend changes

`web/src/routes/plugins.tsx`:

- Add a "状态" badge/button next to each toggle.
- Add a "日志" button next to each row.
- Show transitional state while toggle mutation is pending.
- Add `StatusModal` to display status detail.
- Add `LogModal` to poll and display recent logs.

`web/src/routes/upload.tsx`:

- Fetch `GET /api/plugins?enabled=true` instead of the full list.
- Add a "全选" checkbox in `PluginChecklist`.
- Show empty-state message when no plugins are enabled.

`web/src/lib/api.ts`:

- Add `api.pluginsEnabled()`.
- Add `api.pluginStatus(id)` and `api.pluginLogs(id, limit?)`.

### 5. CLI changes

`hometrove/cli_agent.py`:

- Add `plugins` command with `--enabled` flag.
- Add `describe-plugin <plugin_id>` command.
- Update `upload --plugins` validation to check enabled status before calling `/ingest`.

## Data Models

```python
from enum import Enum
from dataclasses import dataclass

class PluginStatus(str, Enum):
    IDLE = "idle"
    LOADING = "loading"
    ACTIVE = "active"
    ERROR = "error"
    STOPPING = "stopping"

@dataclass
class PluginStatusInfo:
    status: PluginStatus
    detail: str
    loaded_at: int | None
    error_at: int | None

@dataclass
class PluginLogEntry:
    ts: int
    level: str
    message: str
```

## Correctness Properties

1. A disabled plugin never appears in the upload plugin selector.
2. `startup()` and `shutdown()` are mutually exclusive per plugin id.
3. Plugin log files are rotated to prevent unbounded disk growth.
4. CLI validates plugin ids against the enabled list before upload to avoid ingest failures.

## Error Handling

1. IF `startup()` raises, the plugin status becomes `error` with the exception text in `detail`, and `enabled` remains `true` in the database.
2. IF `shutdown()` raises, the plugin status becomes `error` but the config is still persisted as disabled.
3. IF an unknown plugin id is requested, the API returns HTTP 404.
4. IF the CLI requests a disabled/unknown plugin for upload, it prints a clear error and exits non-zero without calling the server.

## Test Strategy

- Backend unit tests for lifecycle manager status transitions.
- API tests for `GET /api/plugins?enabled=true`, status endpoint, and log endpoint.
- Frontend component tests for "全选" behavior and empty state.
- CLI tests for `plugins --enabled`, `describe-plugin`, and disabled-plugin upload rejection.

## References

- `hometrove/plugins/base.py` — BasePlugin contract.
- `hometrove/plugins/registry.py` — in-process registry.
- `hometrove/api/routes/plugins.py` — current plugin REST endpoints.
- `web/src/routes/plugins.tsx` — settings page.
- `web/src/routes/upload.tsx` — upload page.
- `hometrove/cli_agent.py` — agent CLI.
