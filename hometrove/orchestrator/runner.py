"""Per-job runner.

Pulls one job, resolves the plugin, runs it, writes ``plugin_results``.
"""

from __future__ import annotations

import json
import time

from sqlalchemy.orm import Session

from hometrove.config import get_settings
from hometrove.models import Asset, Job, PluginConfig, PluginResult
from hometrove.plugins import (
    AssetLike,
    PluginContext,
    get_plugin,
)


def _now() -> int:
    return int(time.time())


def _params_for(plugin_id: str, session: Session):
    plugin = get_plugin(plugin_id)
    row = session.get(PluginConfig, plugin_id)
    raw: dict = {}
    if row is not None and row.params_json:
        try:
            raw = json.loads(row.params_json)
        except json.JSONDecodeError:
            raw = {}
    return plugin.ParamsModel.model_validate(raw)


def run_one(job_id: int, session: Session) -> None:
    job = session.get(Job, job_id)
    assert job is not None, f"job {job_id} does not exist"
    asset = session.get(Asset, job.asset_id)
    assert asset is not None, f"asset {job.asset_id} does not exist"

    plugin = get_plugin(job.plugin_id)
    params = _params_for(job.plugin_id, session)

    asset_like = AssetLike(
        id=asset.id,
        path=asset.path,
        media_root=asset.media_root,
        media_type=asset.media_type,
        size_bytes=asset.size_bytes,
        mtime=asset.mtime,
        content_hash_prefix=asset.content_hash,
    )
    ctx = PluginContext(asset=asset_like, params=params, db=session, data_dir=get_settings().resolved_data_dir())

    job.state = "running"
    job.started_at = _now()
    job.attempts = (job.attempts or 0) + 1
    t0 = time.monotonic()
    try:
        result_dict = plugin.run(asset_like, ctx)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        pr = session.get(
            PluginResult,
            (asset.id, plugin.id, plugin.version),
        )
        if pr is None:
            pr = PluginResult(
                asset_id=asset.id,
                plugin_id=plugin.id,
                plugin_version=plugin.version,
                status="ok",
            )
            session.add(pr)
        pr.status = "ok"
        pr.result_json = json.dumps(result_dict, ensure_ascii=False, sort_keys=True)
        pr.elapsed_ms = elapsed_ms
        pr.finished_at = _now()

        # Reflect basic.info findings back to the asset row.
        if plugin.id == "basic.info":
            r = result_dict
            if r.get("width") is not None:
                asset.width = r["width"]
            if r.get("height") is not None:
                asset.height = r["height"]
            if r.get("duration_sec") is not None:
                asset.duration_sec = r["duration_sec"]
            if r.get("taken_at") is not None:
                asset.taken_at = r["taken_at"]
            if r.get("size_bytes") is not None and asset.size_bytes is None:
                asset.size_bytes = r["size_bytes"]
            asset.updated_at = _now()

        job.state = "done"
        job.finished_at = _now()
        job.actual_cost = elapsed_ms / 1000.0
        job.error = None
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        pr = session.get(
            PluginResult,
            (asset.id, plugin.id, plugin.version),
        )
        if pr is None:
            pr = PluginResult(
                asset_id=asset.id,
                plugin_id=plugin.id,
                plugin_version=plugin.version,
                status="failed",
                result_json="{}",
            )
            session.add(pr)
        pr.elapsed_ms = elapsed_ms
        pr.finished_at = _now()
        job.state = "failed"
        job.finished_at = _now()
        job.actual_cost = elapsed_ms / 1000.0
        job.error = f"{type(exc).__name__}: {exc}"[:1000]
    session.commit()
