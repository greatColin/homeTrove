# Requirements Document: Plugin Management v2

## Introduction

HomeTrove uses a pluggable analyzer architecture. Today plugins can be enabled/disabled via `PUT /api/plugins/{id}`, but the UI/CLI does not expose runtime status, per-plugin logs, or a clean "enabled-only" selection surface during upload. This feature adds an explicit plugin lifecycle (enabled, loading/starting, active, error), a log viewer, and an enabled-plugin selector on upload, all exposed consistently through the web UI and the agent CLI.

## Glossary

- **Plugin**: A `BasePlugin` subclass registered in `hometrove.plugins.registry.REGISTRY`.
- **Runtime status**: The in-process state of a plugin (`idle`, `loading`, `active`, `error`).
- **PluginConfig**: The persisted row in `plugin_config` storing `enabled`, `params_json`, etc.
- **Load/start hook**: A plugin method (`startup` or equivalent) that initializes heavy resources such as ML models.
- **Agent CLI**: `hometrove-cli`, the HTTP-forwarding command-line client.

## Requirements

### Requirement 1: Enable/disable plugins in settings

**User Story:** AS an administrator, I want to toggle plugins on and off in the settings page, so that only the analyzers I need consume memory and CPU.

#### Acceptance Criteria

1. WHEN the settings page loads, the system SHALL display every registered plugin with its current `enabled` value from `PluginConfig`.
2. WHEN an administrator toggles a plugin switch, the system SHALL call `PUT /api/plugins/{plugin_id}` and persist the new `enabled` value.
3. WHEN a plugin is enabled, the system SHALL invoke the plugin's `startup()` hook if one exists.
4. WHEN a plugin is disabled, the system SHALL invoke the plugin's `shutdown()` hook if one exists.
5. WHILE `startup()` or `shutdown()` is running, the plugin status SHALL display a transitional state (e.g. "启动中" / "停止中").

### Requirement 2: Per-plugin runtime status and logs

**User Story:** AS an administrator, I want to see whether a plugin is ready and inspect its recent logs, so that I can diagnose why a plugin is not working.

#### Acceptance Criteria

1. WHEN the settings page displays a plugin, the system SHALL show a "状态" indicator reflecting the plugin runtime status (`idle`, `loading`, `active`, `error`).
2. WHEN an administrator clicks the "状态" button, the system SHALL open a panel showing the current status, last error message (if any), and resource summary.
3. WHEN the settings page displays a plugin, the system SHALL show a "日志" button.
4. WHEN an administrator clicks the "日志" button, the system SHALL open a panel showing the most recent log entries emitted by that plugin.
5. WHEN a plugin log entry is created, the system SHALL append it to a shared, rotating log file under `HOMETROVE_DATA_DIR/plugin-logs/`.
6. WHEN an administrator requests plugin logs, the system SHALL read the tail of the plugin's log file and return it via `GET /api/plugins/{plugin_id}/logs`.

### Requirement 3: Upload page shows only enabled plugins

**User Story:** AS a user uploading media, I want to select only enabled plugins and use a "select all" shortcut, so that disabled plugins do not clutter the upload form.

#### Acceptance Criteria

1. WHEN the upload page loads, the system SHALL fetch the list of enabled plugins from the backend.
2. WHEN the enabled-plugin list is empty, the upload page SHALL display a message such as "没有启用的插件，请先在设置中启用"。
3. WHEN the enabled-plugin list is non-empty, the system SHALL render a checkbox for each enabled plugin.
4. WHEN the administrator checks a "全选" control, the system SHALL select every enabled plugin.
5. WHEN the administrator unchecks the "全选" control, the system SHALL deselect every enabled plugin.
6. WHEN the user submits an upload, the system SHALL pass the selected plugin ids to `/api/uploads/{upload_id}/ingest`.

### Requirement 4: CLI exposes enabled plugins and descriptions

**User Story:** AS an automation script or LLM agent, I want to query which plugins are enabled and read their descriptions, so that I can decide which plugins to request during upload.

#### Acceptance Criteria

1. WHEN the agent CLI runs `hometrove-cli plugins --enabled`, the system SHALL print a JSON list containing only enabled plugins.
2. EACH item in the enabled-plugin list SHALL include `id`, `name`, `description`, `version`, `supported_media`, and `enabled`.
3. WHEN the agent CLI runs `hometrove-cli describe-plugin <plugin_id>`, the system SHALL print detailed information for that plugin, including its description, parameters schema, and current runtime status.
4. WHEN the agent CLI uploads a file with `--plugins <ids>`, the system SHALL validate that every requested plugin is enabled and return a clear error if any requested plugin is disabled or unknown.

## Out of Scope

- Automatic plugin restart on failure.
- Per-plugin CPU/memory metrics beyond a textual resource summary.
- Centralized log aggregation across multiple machines.
