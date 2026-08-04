"""Bootstrap utilities shared by API and standalone scripts."""

from __future__ import annotations

from hometrove.config import get_settings
from hometrove.db import Base, engine
from hometrove import models  # noqa: F401  register models
from hometrove.models import PluginConfig
from hometrove.plugins.registry import REGISTRY


def ensure_schema() -> None:
    """Create tables if missing. Idempotent."""
    Base.metadata.create_all(engine())


def ensure_default_plugin_configs() -> None:
    """Insert ``plugin_config`` rows for every registered plugin."""
    from hometrove.db import session_scope
    with session_scope() as s:
        for p in REGISTRY.list():
            row = s.get(PluginConfig, p.id)
            if row is None:
                s.add(PluginConfig(plugin_id=p.id, enabled=1))


def bootstrap() -> None:
    """Convenience: ensure schema and default configs exist."""
    ensure_schema()
    ensure_default_plugin_configs()
