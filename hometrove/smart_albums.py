"""Smart album rule validation and evaluation.

A smart album stores a JSON rule. On every read the rule is translated into
a set of asset ids. Supported operators:

- and/or: combine child rules
- person: face_embeddings.person_id == person_id
- place: exif plugin result GPS falls inside the place grid cell
- tag/category: substring match in mock.tags / mock.category result
- time: taken_at inside [after, before]
- media_type: assets.media_type == value
- favorite: assets.favorite == (1 if value else 0)
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.models import Asset, FaceCluster, FaceEmbedding, PluginResult

_ALLOWED_OPS = frozenset(
    {"and", "or", "person", "place", "tag", "category", "time", "media_type", "favorite"}
)


def validate_rule(rule: dict[str, Any]) -> None:
    """Validate a rule expression, raising HTTPException 422 on failure."""

    def _walk(node: Any, depth: int) -> None:
        if not isinstance(node, dict):
            raise HTTPException(422, "rule node must be an object")
        if depth > 3:
            raise HTTPException(422, "rule nesting exceeds 3 levels")
        op = node.get("op")
        if op not in _ALLOWED_OPS:
            raise HTTPException(422, f"unsupported rule operator: {op!r}")

        if op in ("and", "or"):
            children = node.get("children")
            if not isinstance(children, list) or len(children) == 0:
                raise HTTPException(422, f"{op} requires a non-empty children list")
            for child in children:
                _walk(child, depth + 1)
        elif op == "person":
            if not isinstance(node.get("person_id"), int):
                raise HTTPException(422, "person rule requires integer person_id")
        elif op == "place":
            if not isinstance(node.get("place_id"), str):
                raise HTTPException(422, "place rule requires string place_id")
        elif op in ("tag", "category"):
            if not isinstance(node.get("value"), str):
                raise HTTPException(422, f"{op} rule requires string value")
        elif op == "time":
            after = node.get("after")
            before = node.get("before")
            if after is not None and not isinstance(after, int):
                raise HTTPException(422, "time.after must be an integer epoch")
            if before is not None and not isinstance(before, int):
                raise HTTPException(422, "time.before must be an integer epoch")
            if after is not None and before is not None and after > before:
                raise HTTPException(422, "time.after must be <= time.before")
        elif op == "media_type":
            if node.get("value") not in ("image", "video", "other"):
                raise HTTPException(422, "media_type value must be image/video/other")
        elif op == "favorite":
            if not isinstance(node.get("value"), bool):
                raise HTTPException(422, "favorite rule requires boolean value")

    _walk(rule, 0)


def eval_rule(session: Session, rule: dict[str, Any]) -> list[int]:
    """Evaluate a rule and return matching live asset ids ordered by taken_at desc, id desc."""

    def _eval(node: dict[str, Any]) -> set[int]:
        op = node["op"]

        if op == "and":
            result: set[int] | None = None
            for child in node["children"]:
                child_ids = _eval(child)
                result = child_ids if result is None else (result & child_ids)
            return result or set()

        if op == "or":
            result: set[int] = set()
            for child in node["children"]:
                result |= _eval(child)
            return result

        if op == "person":
            rows = session.execute(
                select(FaceEmbedding.asset_id)
                .join(FaceCluster, FaceCluster.id == FaceEmbedding.cluster_id)
                .where(FaceCluster.person_id == node["person_id"])
            ).scalars().all()
            return set(rows)

        if op == "tag":
            return _facet_ids(session, "mock.tags", node["value"])

        if op == "category":
            return _facet_ids(session, "mock.category", node["value"])

        if op == "media_type":
            rows = session.execute(
                select(Asset.id).where(
                    Asset.media_type == node["value"],
                    Asset.deleted_at.is_(None),
                )
            ).scalars().all()
            return set(rows)

        if op == "favorite":
            rows = session.execute(
                select(Asset.id).where(
                    Asset.favorite == (1 if node["value"] else 0),
                    Asset.deleted_at.is_(None),
                )
            ).scalars().all()
            return set(rows)

        if op == "time":
            stmt = select(Asset.id).where(Asset.deleted_at.is_(None))
            after = node.get("after")
            before = node.get("before")
            if after is not None:
                stmt = stmt.where(Asset.taken_at >= after)
            if before is not None:
                stmt = stmt.where(Asset.taken_at <= before)
            rows = session.execute(stmt).scalars().all()
            return set(rows)

        if op == "place":
            return _place_ids(session, node["place_id"])

        raise HTTPException(422, f"unsupported rule operator: {op!r}")

    ids = _eval(rule)
    if not ids:
        return []

    rows = session.execute(
        select(Asset.id)
        .where(Asset.id.in_(ids), Asset.deleted_at.is_(None))
        .order_by(Asset.taken_at.desc().nulls_last(), Asset.id.desc())
    ).scalars().all()
    return list(rows)


def _facet_ids(session: Session, plugin_id: str, value: str) -> set[int]:
    rows = session.execute(
        select(PluginResult.asset_id).where(
            PluginResult.plugin_id == plugin_id,
            PluginResult.result_json.contains(f'"{value}"'),
        )
    ).scalars().all()
    return set(rows)


def _place_ids(session: Session, place_id: str) -> set[int]:
    """Match assets whose exif GPS falls inside the given place grid cell.

    ``place_id`` is encoded as ``{lat},{lon}`` where lat/lon are the grid cell
    centre coordinates produced by ``hometrove.api.routes.places``.
    """
    try:
        lat_s, lon_s = place_id.split(",")
        centre_lat = float(lat_s)
        centre_lon = float(lon_s)
    except (ValueError, AttributeError):
        return set()

    from hometrove.api.routes.places import _GRID, _cluster_key

    grid = _GRID
    centre_lat, centre_lon = _cluster_key(centre_lat, centre_lon, grid)
    rows = session.execute(
        select(PluginResult.asset_id, PluginResult.result_json).where(
            PluginResult.plugin_id == "exif",
            PluginResult.status == "ok",
        )
    ).all()

    ids: set[int] = set()
    for asset_id, result_json in rows:
        try:
            data = json.loads(result_json or "{}")
        except json.JSONDecodeError:
            continue
        metadata = data.get("metadata") if isinstance(data, dict) else None
        if not isinstance(metadata, dict):
            continue
        lat = metadata.get("gps_lat")
        lon = metadata.get("gps_lon")
        if lat is None or lon is None:
            continue
        key = _cluster_key(float(lat), float(lon), grid)
        if abs(key[0] - centre_lat) < 1e-9 and abs(key[1] - centre_lon) < 1e-9:
            ids.add(asset_id)
    return ids
