"""``face.match`` — group detected face vectors under people.

This plugin consumes another plugin's detection output (in M0, ``mock.faces``;
later a real detector) from ``plugin_results``, and for each face vector calls
the library matcher. It is deliberately thin: the grouping logic lives in
``hometrove.faces`` and is also used by the person-management API (naming
backfill, manual merge).

Unlike ``mock.*`` this is a *real* plugin — it stays when mocks are removed.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from hometrove.plugins.api import AssetLike, Cost, MediaType, PluginContext
from hometrove.plugins.base import BasePlugin


class FaceMatchPlugin(BasePlugin):
    id: str = "face.match"
    name: str = "人脸归组"
    version: str = "0.1.0"
    supported_media: set[str] = {MediaType.IMAGE.value}
    depends_on: list[str] = ["mock.faces"]

    class ParamsModel(BaseModel):
        threshold: float = 0.75

    def estimate(self, asset: AssetLike) -> Cost:
        return Cost(seconds=0.001, device="cpu")

    def run(self, asset: AssetLike, ctx: PluginContext) -> dict[str, Any]:
        if ctx.db is None:
            return {"grouped": 0, "error": "no database context"}

        # Read the detector's output for this asset.
        from hometrove.models import PluginResult

        pr = None
        for plugin_id in ("mock.faces", "faces.detect"):
            pr = ctx.db.get(PluginResult, (asset.id, plugin_id, "0.1.0"))
            if pr is not None and pr.status == "ok":
                break
        if pr is None:
            return {"grouped": 0, "detected": 0}

        try:
            faces = json.loads(pr.result_json or "{}").get("faces", [])
        except json.JSONDecodeError:
            faces = []

        params: FaceMatchPlugin.ParamsModel = ctx.params  # type: ignore[assignment]
        from hometrove.faces import match_asset_faces
        grouped = match_asset_faces(
            ctx.db,
            asset.id,
            faces,
            threshold=params.threshold,
        )
        return {"grouped": grouped, "detected": len(faces)}
