from __future__ import annotations

import os
import struct
import tempfile
import zlib
from pathlib import Path

import pytest

from hometrove.config import reset_settings_cache
from hometrove.plugins import REGISTRY
import hometrove.plugins.builtin  # noqa: F401  register built-ins


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOMETROVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOMETROVE_MEDIA_ROOTS", "")
    reset_settings_cache()
    from hometrove.db import reset_engine
    reset_engine()
    yield tmp_path / "data"
    reset_settings_cache()
    reset_engine()


def make_png(path: Path, w: int = 32, h: int = 24) -> None:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    raw = b""
    for _ in range(h):
        raw += b"\x00" + b"\xff\x00\x00" * w
    path.write_bytes(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_classify_extensions():
    from hometrove.plugins.builtin.basic_info import classify

    assert classify(Path("a.JPG")).name == "IMAGE"
    assert classify(Path("a.JPG")).value == "image"
    assert classify(Path("a.mp4")).value == "video"
    assert classify(Path("a.MOV")).value == "video"
    assert classify(Path("a.txt")).value == "other"


def test_read_png_dimensions(tmp_path: Path):
    from hometrove.plugins.builtin.basic_info import _read_image_dimensions

    p = tmp_path / "img.png"
    make_png(p, 64, 48)
    assert _read_image_dimensions(p) == (64, 48)


def test_read_dimensions_returns_none_for_missing(tmp_path: Path):
    from hometrove.plugins.builtin.basic_info import _read_image_dimensions

    assert _read_image_dimensions(tmp_path / "missing.png") == (None, None)


def test_basic_info_plugin_run(tmp_data_dir, tmp_path: Path):
    from hometrove.models import Asset, Job, PluginResult
    from hometrove.db import session_scope, engine
    from hometrove.orchestrator.runner import run_one
    from hometrove.scanner import discover, upsert_assets, enqueue_basic_info

    engine()  # bootstrap schema
    media = tmp_path / "photos"
    media.mkdir()
    make_png(media / "a.png", 100, 80)

    with session_scope() as s:
        new, skipped = upsert_assets(s, list(discover([media])))
        assert new == 1
        assert skipped == 0
        assert enqueue_basic_info(s) == 1

    with session_scope() as s:
        job = s.query(Job).filter(Job.state == "pending").first()
        assert job is not None
        run_one(job.id, s)
        s.refresh(job)
        assert job.state == "done"
        pr = s.query(PluginResult).one()
        assert pr.status == "ok"
        import json

        r = json.loads(pr.result_json)
        assert r["name"] == "a.png"
        assert r["width"] == 100
        assert r["height"] == 80
        a = s.get(Asset, pr.asset_id)
        assert a.width == 100 and a.height == 80


def test_idempotent_scan(tmp_data_dir, tmp_path: Path):
    from hometrove.db import session_scope, engine
    from hometrove.scanner import discover, upsert_assets, enqueue_basic_info

    engine()
    media = tmp_path / "photos"
    media.mkdir()
    make_png(media / "a.png", 10, 10)
    with session_scope() as s:
        upsert_assets(s, list(discover([media])))
        assert enqueue_basic_info(s) == 1
    # second pass is a no-op
    with session_scope() as s:
        new, skipped = upsert_assets(s, list(discover([media])))
        assert new == 0 and skipped == 1
        # and no extra jobs
        assert enqueue_basic_info(s) == 0


def test_dag_single_node(tmp_data_dir):
    from hometrove.orchestrator.dag import Node, build_graph

    ts, by_id = build_graph([Node(plugin_id="basic.info")])
    assert "basic.info" in by_id
    # ``prepare()`` then ``done()`` is the full cycle.
    ts.prepare()
    assert ts.is_active() is True
    ready = list(ts.get_ready())
    assert ready == ["basic.info"]
    ts.done(ready[0])
    assert ts.is_active() is False


def test_health_endpoint(tmp_data_dir):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import engine

    engine()
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_asset_file_stream(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import engine, session_scope
    from hometrove.models import Asset
    from hometrove.scanner import discover, upsert_assets

    engine()
    media = tmp_path / "photos"
    media.mkdir()
    make_png(media / "a.png", 8, 6)
    with session_scope() as s:
        upsert_assets(s, list(discover([media])))

    app = create_app()
    with TestClient(app) as c:
        # Locate the asset id
        with session_scope() as s:
            aid = s.query(Asset).first().id
        r = c.get(f"/api/assets/{aid}/file")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    # Path traversal guard: a crafted path column must not escape the root.
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    with session_scope() as s:
        evil = Asset(
            path=f"{media}\0../../etc/passwd",
            media_root=str(media),
            content_hash="x",
            media_type="other",
            size_bytes=0,
            mtime=0,
            created_at=0,
            updated_at=0,
        )
        s.add(evil)
        s.commit()
        eid = evil.id
    with TestClient(app) as c:
        r = c.get(f"/api/assets/{eid}/file")
        assert r.status_code == 404


def test_chunked_upload_roundtrip(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import engine

    engine()
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/uploads", json={"filename": "big.bin", "size": 11, "content_hash": None})
        assert r.status_code == 200
        sid = r.json()["upload_id"]
        # Send two chunks
        r = c.put(f"/api/uploads/{sid}/chunks/0", files={"file": ("c.bin", b"hello", "application/octet-stream")})
        assert r.status_code == 200
        r = c.put(f"/api/uploads/{sid}/chunks/1", files={"file": ("c.bin", b" world", "application/octet-stream")})
        assert r.status_code == 200

        # Simulate a resume: ask which chunks are on disk
        r = c.get(f"/api/uploads/{sid}")
        assert r.status_code == 200
        assert sorted(r.json()["uploaded_chunks"]) == [0, 1]

        # Finalize
        r = c.post(f"/api/uploads/{sid}/complete", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "big.bin"
        assert Path(body["staging_path"]).read_bytes() == b"hello world"

        # Mismatched size -> 409
        r = c.post("/api/uploads", json={"filename": "x", "size": 99, "content_hash": None})
        sid2 = r.json()["upload_id"]
        c.put(f"/api/uploads/{sid2}/chunks/0", files={"file": ("c", b"abc", "application/octet-stream")})
        c.put(f"/api/uploads/{sid2}/chunks/1", files={"file": ("c", b"def", "application/octet-stream")})
        r = c.post(f"/api/uploads/{sid2}/complete", json={"chunk_indices": [0, 1]})
        assert r.status_code == 409


def _seed_library(tmp_data_dir, tmp_path: Path, n: int = 4) -> int:
    """Insert ``n`` png assets and run all plugins via the runner. Returns asset count."""
    from hometrove.db import engine, session_scope
    from hometrove.scanner import discover, enqueue_pending, upsert_assets
    from hometrove.orchestrator.runner import run_one
    from hometrove.models import Job

    engine()
    media = tmp_path / "photos"
    media.mkdir()
    for i in range(n):
        make_png(media / f"img{i}.png", 10 + i, 8)
    with session_scope() as s:
        upsert_assets(s, list(discover([media])))
        assert enqueue_pending(s) == n * len(REGISTRY.list())
    # Consume the queue exactly like the worker does.
    for _ in range(n * len(REGISTRY.list())):
        with session_scope() as s:
            job = s.query(Job).filter(Job.state == "pending").order_by(Job.enqueued_at).first()
            assert job is not None
            job.state = "running"
            s.commit()
            run_one(job.id, s)
    return n


def test_mock_plugins_idempotent_and_deterministic(tmp_data_dir, tmp_path: Path):
    from hometrove.plugins.api import AssetLike
    from hometrove.plugins.mock import MockTagsPlugin

    p = MockTagsPlugin()
    a1 = AssetLike(id=7, path="/p/a.png", media_root="/p", media_type="image", content_hash_prefix="h1")
    a2 = AssetLike(id=7, path="/p/a.png", media_root="/p", media_type="image", content_hash_prefix="h1")
    r1 = p.run(a1, type("C", (), {"params": p.ParamsModel.model_validate({})})())
    r2 = p.run(a2, type("C", (), {"params": p.ParamsModel.model_validate({})})())
    assert r1 == r2  # deterministic for same asset


def test_facets_and_facet_filter(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_library(tmp_data_dir, tmp_path, n=4)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/facets")
        assert r.status_code == 200
        body = r.json()
        for key in ("tags", "categories", "persons"):
            assert key in body
        assert body["tags"]  # mock.tags produced some tags

        # Pick one tag and filter assets by it — must return 200 and some items.
        tag = next(iter(body["tags"]))
        r = c.get("/api/assets", params={"tag": tag, "limit": 50})
        assert r.status_code == 200
        page = r.json()
        assert page["items"]

        # Every returned asset must actually carry that tag.
        for item in page["items"]:
            detail = c.get(f"/api/assets/{item['id']}").json()
            pr = detail["plugin_results"]
            assert "mock.tags" in pr
            assert tag in pr["mock.tags"]["data"]["tags"]

        # Unknown facet value -> empty result, valid request.
        r = c.get("/api/assets", params={"tag": "__never_exists__", "limit": 50})
        assert r.status_code == 200
        assert r.json()["items"] == []


def test_asset_detail_includes_all_plugin_results(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_library(tmp_data_dir, tmp_path, n=2)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/assets/1")
        assert r.status_code == 200
        body = r.json()
        assert "plugin_results" in body
        for pid in ("basic.info", "mock.tags", "mock.category", "mock.faces"):
            assert pid in body["plugin_results"], f"missing {pid}"
            assert body["plugin_results"][pid]["status"] == "ok"
        # Dynamic fields live under each plugin's ``data``.
        assert "tags" in body["plugin_results"]["mock.tags"]["data"]
        assert "category" in body["plugin_results"]["mock.category"]["data"]
        assert "faces" in body["plugin_results"]["mock.faces"]["data"]
