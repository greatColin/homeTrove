"""Upload plugin preset management.

Built-in presets are seeded on first access; they cannot be deleted.
User-created presets can be CRUD'd freely.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import PluginPreset

router = APIRouter(prefix="/api/upload-presets", tags=["upload-presets"])

_BUILTIN_PRESETS = [
    {
        "name": "默认",
        "plugin_ids": [],  # empty → run all globally enabled plugins
        "is_builtin": True,
    },
    {
        "name": "会议",
        "plugin_ids": [
            "thumbnail",
            "exif",
            "basic.scene_detect",
            "basic.info",
        ],
        "is_builtin": True,
    },
    {
        "name": "旅游",
        "plugin_ids": [
            "thumbnail",
            "exif",
            "basic.scene_detect",
            "embedding.jina_clip",
            "basic.info",
        ],
        "is_builtin": True,
    },
]


def _ensure_builtins(session: Session) -> None:
    """Seed built-in presets on first access."""
    existing = {r.name for r in session.execute(select(PluginPreset)).scalars().all()}
    for spec in _BUILTIN_PRESETS:
        if spec["name"] not in existing:
            session.add(
                PluginPreset(
                    name=spec["name"],
                    is_builtin=1,
                    plugin_ids=json.dumps(spec["plugin_ids"], ensure_ascii=False),
                )
            )
    session.commit()


def _preset_dto(p: PluginPreset) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "is_builtin": bool(p.is_builtin),
        "plugin_ids": json.loads(p.plugin_ids) if p.plugin_ids else [],
        "created_at": p.created_at,
    }


@router.get("")
def list_presets(session: Session = Depends(get_db)):
    _ensure_builtins(session)
    rows = session.execute(select(PluginPreset).order_by(PluginPreset.id)).scalars().all()
    return {"items": [_preset_dto(r) for r in rows]}


class CreatePreset(BaseModel):
    name: str
    plugin_ids: list[str]


@router.post("", status_code=201)
def create_preset(body: CreatePreset, session: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "name must not be blank")
    existing = session.execute(
        select(PluginPreset).where(PluginPreset.name == name)
    ).scalars().first()
    if existing is not None:
        raise HTTPException(409, "a preset with this name already exists")
    p = PluginPreset(
        name=name,
        is_builtin=0,
        plugin_ids=json.dumps(body.plugin_ids, ensure_ascii=False),
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return _preset_dto(p)


@router.delete("/{preset_id}")
def delete_preset(preset_id: int, session: Session = Depends(get_db)):
    p = session.get(PluginPreset, preset_id)
    if p is None:
        raise HTTPException(404, "preset not found")
    if p.is_builtin:
        raise HTTPException(403, "built-in presets cannot be deleted")
    session.delete(p)
    session.commit()
    return {"ok": True}
