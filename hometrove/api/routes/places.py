"""Place aggregation API.

Photos / videos with GPS coordinates in their ``exif`` plugin result are
clustered into a coarse lat/lon grid (default ~0.5 deg, roughly 50 km) so the
frontend can render a dot map and a cluster list without a geocoder.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from hometrove.db import get_db
from hometrove.models import Asset, PluginResult


router = APIRouter(prefix="/api/places", tags=["places"])

_GRID = 0.5


def _cluster_key(lat: float, lon: float, grid: float) -> tuple[float, float]:
    """Snap a coordinate to the nearest grid cell centre."""
    return (round(lat / grid) * grid, round(lon / grid) * grid)


@router.get("")
def places(
    grid: float = Query(_GRID, ge=0.01, le=5.0, description="grid cell size in degrees"),
    session: Session = Depends(get_db),
):
    rows = session.execute(
        select(PluginResult, Asset)
        .join(Asset, Asset.id == PluginResult.asset_id)
        .where(
            PluginResult.plugin_id == "exif",
            PluginResult.status == "ok",
            Asset.deleted_at.is_(None),
        )
    ).all()

    clusters: dict[tuple[float, float], dict] = {}
    for pr, _asset in rows:
        try:
            data = json.loads(pr.result_json or "{}")
        except json.JSONDecodeError:
            continue
        lat = data.get("metadata", {}).get("gps_lat") if isinstance(data.get("metadata"), dict) else None
        lon = data.get("metadata", {}).get("gps_lon") if isinstance(data.get("metadata"), dict) else None
        if lat is None or lon is None:
            continue
        key = _cluster_key(float(lat), float(lon), grid)
        c = clusters.setdefault(key, {"lat": 0.0, "lon": 0.0, "count": 0, "asset_ids": []})
        c["count"] += 1
        c["lat"] += float(lat)
        c["lon"] += float(lon)
        c["asset_ids"].append(pr.asset_id)

    items = []
    for key, c in clusters.items():
        n = c["count"]
        items.append(
            {
                "grid": key,
                "lat": c["lat"] / n,
                "lon": c["lon"] / n,
                "count": n,
                "asset_ids": sorted(c["asset_ids"]),
            }
        )
    items.sort(key=lambda x: -x["count"])
    return {"items": items, "grid": grid}
