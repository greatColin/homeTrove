from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from hometrove.config import reset_settings_cache
import hometrove.plugins.builtin  # noqa: F401  register built-ins


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOMETROVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOMETROVE_MEDIA_ROOTS", "")
    reset_settings_cache()
    from hometrove.db import reset_engine

    reset_engine()
    from hometrove.vault.state import _reset_for_test

    _reset_for_test()  # noqa: SLF001
    monkeypatch.setattr("hometrove.plugins.builtin.face_detect._get_app", lambda: None)
    yield tmp_path / "data"
    reset_settings_cache()
    reset_engine()
    _reset_for_test()  # noqa: SLF001


def make_png(path: Path, w: int = 32, h: int = 24) -> None:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw = b""
    for _ in range(h):
        raw += b"\x00" + b"\xff\x00\x00" * w
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture
def client(tmp_data_dir, tmp_path: Path):
    from hometrove.api import create_app
    from hometrove.db import engine

    engine()  # bootstrap schema
    app = create_app()
    media = tmp_path / "photos"
    media.mkdir()
    (tmp_path / "ignored.txt").write_text("not media")
    make_png(media / "a.png", 10, 10)
    make_png(media / "b.png", 20, 20)

    import os

    os.environ["HOMETROVE_MEDIA_ROOTS"] = str(media)
    reset_settings_cache()

    with TestClient(app) as c:
        yield c

    reset_settings_cache()


def test_scan_does_not_auto_enqueue(client: TestClient):
    resp = client.post("/api/scan")
    assert resp.status_code == 200
    data = resp.json()
    assert data["new"] == 2
    assert data["skipped"] == 0
    assert "enqueued" not in data

    jobs = client.get("/api/jobs").json()
    assert jobs["stats"]["total"] == 0


def test_queue_stats_for_image_plugin(client: TestClient):
    client.post("/api/scan")
    resp = client.get("/api/queue/stats?media_type=image")
    assert resp.status_code == 200
    data = resp.json()
    assert data["media_type"] == "image"
    basic = next((p for p in data["plugins"] if p["plugin_id"] == "basic.info"), None)
    assert basic is not None
    assert basic["todo"] == 2
    assert basic["active"] == 0
    assert basic["failed"] == 0
    assert basic["done"] == 0


def test_assets_paginated_by_plugin_state(client: TestClient):
    client.post("/api/scan")
    resp = client.get("/api/queue/assets?plugin_id=basic.info&media_type=image&state=todo&page=1&size=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["size"] == 1
    assert data["pages"] == 2
    assert len(data["items"]) == 1
    assert data["items"][0]["filename"] in ("a.png", "b.png")


def test_enqueue_assets_and_idempotency(client: TestClient):
    client.post("/api/scan")
    assets = client.get(
        "/api/queue/assets?plugin_id=basic.info&media_type=image&state=todo&page=1&size=50"
    ).json()
    ids = [a["id"] for a in assets["items"]]

    resp = client.post("/api/queue/enqueue", json={"plugin_id": "basic.info", "asset_ids": ids})
    assert resp.status_code == 200
    assert resp.json()["created"] == 2

    # Re-enqueue while pending is a no-op.
    resp = client.post("/api/queue/enqueue", json={"plugin_id": "basic.info", "asset_ids": ids})
    assert resp.status_code == 200
    assert resp.json()["created"] == 0
    assert resp.json()["skipped"] == 2

    jobs = client.get("/api/jobs").json()
    assert jobs["stats"]["pending"] == 2


def test_done_state_filter_after_run(client: TestClient):
    from hometrove.db import session_scope
    from hometrove.models import Job
    from hometrove.orchestrator.runner import run_one

    client.post("/api/scan")
    assets = client.get(
        "/api/queue/assets?plugin_id=basic.info&media_type=image&state=todo&page=1&size=50"
    ).json()
    ids = [a["id"] for a in assets["items"]]
    client.post("/api/queue/enqueue", json={"plugin_id": "basic.info", "asset_ids": ids})

    with session_scope() as s:
        job = s.execute(select(Job).where(Job.plugin_id == "basic.info")).scalars().first()
        assert job is not None
        run_one(job.id, s)

    resp = client.get("/api/queue/assets?plugin_id=basic.info&media_type=image&state=done")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    stats = client.get("/api/queue/stats?media_type=image").json()
    basic = next(p for p in stats["plugins"] if p["plugin_id"] == "basic.info")
    assert basic["done"] == 1
    assert basic["active"] == 1
    assert basic["todo"] == 0
