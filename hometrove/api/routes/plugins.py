from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import PluginConfig
from hometrove.plugins.registry import REGISTRY

router = APIRouter(prefix="/api", tags=["plugins"])


def _plugin_dto(p, row: PluginConfig | None) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "version": p.version,
        "supported_media": sorted(p.supported_media),
        "depends_on": list(p.depends_on),
        "enabled": bool(row.enabled) if row is not None else True,
        "params": p.ParamsModel.model_json_schema(),
    }


@router.get("/plugins")
def list_plugins(session: Session = Depends(get_db)):
    rows = {
        r.plugin_id: r
        for r in session.query(PluginConfig).all()
    }
    plugins = []
    for p in REGISTRY.list():
        plugins.append(_plugin_dto(p, rows.get(p.id)))
    # Deterministic order: registry order, not dict hashing.
    plugins.sort(key=lambda x: x["id"])
    return {"items": plugins}


class PluginUpdate(BaseModel):
    enabled: bool


@router.put("/plugins/{plugin_id}")
def update_plugin(plugin_id: str, body: PluginUpdate, session: Session = Depends(get_db)):
    plugin = REGISTRY.get(plugin_id) if plugin_id in {
        p.id for p in REGISTRY.list()
    } else None
    if plugin is None:
        raise HTTPException(404, "unknown plugin")

    row = session.get(PluginConfig, plugin_id)
    if row is None:
        row = PluginConfig(plugin_id=plugin_id, enabled=1 if body.enabled else 0)
        session.add(row)
    else:
        row.enabled = 1 if body.enabled else 0
    session.commit()

    # Disabling a plugin should release its in-memory resources (e.g. loaded
    # models). On-disk artifacts are deliberately untouched.
    if not body.enabled:
        try:
            plugin.shutdown()
        except Exception:  # noqa: BLE001  — shutdown must not break the API call
            pass

    fresh = session.get(PluginConfig, plugin_id)
    return _plugin_dto(plugin, fresh)
