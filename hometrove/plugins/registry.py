"""Plugin registry.

M0 keeps a single in-process registry. The narrow surface is what plugins
call at import time and what the orchestrator uses to look up plugins.
"""

from __future__ import annotations

from hometrove.plugins.base import BasePlugin


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, BasePlugin] = {}

    def register(self, plugin: BasePlugin) -> None:
        if not plugin.id:
            raise ValueError("plugin.id must be non-empty")
        if plugin.id in self._plugins:
            existing = self._plugins[plugin.id]
            if existing.version != plugin.version:
                # Re-registration with a different version is allowed — it is the
                # mechanism for the ``plugin_config`` table's
                # ``on-version-bump reprocess`` behavior. We surface this for now.
                pass
        self._plugins[plugin.id] = plugin

    def get(self, plugin_id: str) -> BasePlugin:
        return self._plugins[plugin_id]

    def list(self) -> list[BasePlugin]:
        return list(self._plugins.values())

    def clear(self) -> None:
        self._plugins.clear()


REGISTRY = PluginRegistry()


def register(plugin: BasePlugin) -> None:
    REGISTRY.register(plugin)


def get_plugin(plugin_id: str) -> BasePlugin:
    return REGISTRY.get(plugin_id)


def list_plugins() -> list[BasePlugin]:
    return REGISTRY.list()
