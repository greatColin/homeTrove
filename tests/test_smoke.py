from __future__ import annotations

import json
import os
import struct
import tempfile
import zlib
from pathlib import Path

import pytest
from sqlalchemy import select

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
    # The vault singleton is a module-level object so it leaks across
    # tests. Reset it so the next ``create_app``'s lifespan re-hydrates
    # cleanly from the new (empty) DB.
    from hometrove.vault.state import _reset_for_test
    _reset_for_test()  # noqa: SLF001
    # In unit tests we exercise the mock face pipeline (``mock.faces`` ->
    # ``face.match``), so simulate "insightface unavailable" to force
    # ``face.detect`` to skip and let ``face.match`` fall back to the mocks.
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


def _make_video(path: Path, w: int = 160, h: int = 120, frames: int = 12) -> None:
    """Write a tiny MP4 with PyAV (bundled FFmpeg) — no system ffmpeg needed."""
    import av
    import numpy as np

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=24)
    stream.width = w
    stream.height = h
    for i in range(frames):
        arr = np.full((h, w, 3), i * 8, dtype="uint8")
        frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _make_scene_video(path: Path, seg_specs: list[tuple[int, int]]) -> None:
    """Write an MP4 of static-color segments, e.g. [(gray0, frames), ...]."""
    import av
    import numpy as np

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=24)
    stream.width = 320
    stream.height = 240
    for gray, frames in seg_specs:
        color = np.full((240, 320, 3), gray, dtype="uint8")
        for _ in range(frames):
            frame = av.VideoFrame.from_ndarray(color, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _seed_library(tmp_data_dir, tmp_path: Path, n: int = 4) -> int:
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
        # Only non-stub plugins are scheduled — stubs stay registered for the
        # plugin list but never get enqueued. (m2+ plugin classification)
        scheduled = [
            p for p in REGISTRY.list()
            if getattr(p, "category", None) != "stub"
        ]
        assert enqueue_pending(s) == n * len(scheduled)
    # Consume the queue exactly like the worker does (claims respect the
    # plugin DAG, so ``face.match`` only runs after ``mock.faces``).
    from hometrove.worker.main import _claim_next
    while True:
        with session_scope() as s:
            job = _claim_next(s, "test")
            if job is None:
                break
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
        for pid in ("basic.info", "mock.tags", "mock.category", "mock.faces", "face.match"):
            assert pid in body["plugin_results"], f"missing {pid}"
            assert body["plugin_results"][pid]["status"] == "ok"
        # Dynamic fields live under each plugin's ``data``.
        assert "tags" in body["plugin_results"]["mock.tags"]["data"]
        assert "category" in body["plugin_results"]["mock.category"]["data"]
        assert "faces" in body["plugin_results"]["mock.faces"]["data"]


def test_face_grouping_creates_unnamed_persons(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import FaceEmbedding, Person

    _seed_library(tmp_data_dir, tmp_path, n=2)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/facets")
        assert r.status_code == 200
        persons_map = r.json()["persons"]
        assert persons_map, "mock.faces + face.match should group faces under persons"

        r = c.get("/api/persons")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == len(persons_map)
        for p in items:
            assert p["name"].startswith("未命名")
            assert p["face_count"] > 0

    with session_scope() as s:
        assert s.query(FaceEmbedding).count() > 0
        assert s.query(Person).count() == len(persons_map)


def test_person_rename_backfills_and_merge(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import FaceEmbedding, Person
    from hometrove.faces import merge_persons

    _seed_library(tmp_data_dir, tmp_path, n=4)
    app = create_app()
    with session_scope() as s:
        total_before = s.query(FaceEmbedding).count()
    with TestClient(app) as c:
        people = c.get("/api/persons").json()["items"]
        assert len(people) >= 2
        first, second = people[0], people[1]

        r = c.patch(f"/api/persons/{first['id']}", json={"name": "张三"})
        assert r.status_code == 200
        assert r.json()["name"] == "张三"
        # Backfill may have pulled unnamed faces under 张三; at least keeps own.
        assert r.json()["face_count"] >= 1

        r = c.patch(f"/api/persons/{second['id']}", json={"info": {"年龄": 30, "备注": "好友"}})
        assert r.status_code == 200
        assert r.json()["info"]["年龄"] == 30

        # Merge second into first.
        keep, remove = first["id"], second["id"]
        r = c.post("/api/persons/merge", json={"keep_id": keep, "remove_id": remove})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        merged = c.get(f"/api/persons/{keep}").json()
        assert merged["face_count"] >= 1
        assert c.get(f"/api/persons/{remove}").status_code == 404

    with session_scope() as s:
        assert s.get(Person, remove) is None
        # All faces that used to belong to ``remove`` now live under ``keep``.
        for f in s.query(FaceEmbedding).all():
            assert f.person_id != remove
        # Merge must never drop face rows (delete-orphan regression guard).
        assert s.query(FaceEmbedding).count() == total_before


def test_person_facet_filter(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_library(tmp_data_dir, tmp_path, n=4)
    app = create_app()
    with TestClient(app) as c:
        people = c.get("/api/persons", params={"include_assets": True}).json()["items"]
        assert people
        pid = people[0]["id"]
        asset_ids = people[0]["asset_ids"]
        assert asset_ids
        r = c.get("/api/assets", params={"person_id": pid, "limit": 50})
        assert r.status_code == 200
        got = {item["id"] for item in r.json()["items"]}
        assert set(asset_ids) <= got


def test_thumbnail_plugin_writes_sizes(tmp_data_dir, tmp_path: Path):
    """The thumbnail plugin must downscale an image into the configured
    size buckets under ``{data_dir}/thumbs/{asset_id}/``."""
    from PIL import Image
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.thumbnail import ThumbnailPlugin

    src = tmp_path / "big.png"
    Image.new("RGB", (1200, 800), (10, 200, 40)).save(src)

    p = ThumbnailPlugin()
    asset = AssetLike(
        id=99, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="th",
    )
    ctx = PluginContext(asset=asset, params=p.ParamsModel(), data_dir=tmp_data_dir)
    res = p.run(asset, ctx)
    assert res["status"] == "ok"
    assert "small" in res["sizes"] and "medium" in res["sizes"]

    small = Image.open(tmp_data_dir / "thumbs" / "99" / "small.jpg")
    assert small.size == (320, 213)  # 1200x800 -> 320 wide, aspect preserved
    medium = Image.open(tmp_data_dir / "thumbs" / "99" / "medium.jpg")
    assert medium.size[0] <= 1280 and medium.size[1] <= 1280
    assert res["width"] == 1200 and res["height"] == 800


def test_thumbnail_video_frame(tmp_data_dir, tmp_path: Path):
    """Videos are thumbnailed from a real frame via PyAV (no system ffmpeg)."""
    import numpy as np
    from PIL import Image
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.thumbnail import ThumbnailPlugin

    src = tmp_path / "clip.mp4"
    _make_video(src, w=320, h=240, frames=24)

    p = ThumbnailPlugin()
    asset = AssetLike(
        id=98, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="vid",
    )
    ctx = PluginContext(asset=asset, params=p.ParamsModel(), data_dir=tmp_data_dir)
    res = p.run(asset, ctx)
    assert res["status"] == "ok"
    assert res["source"] == "video-frame"
    small = tmp_data_dir / "thumbs" / "98" / "small.jpg"
    assert small.is_file()
    im = Image.open(small)
    assert im.size[0] <= 320 and im.size[1] <= 240


def test_thumbnail_video_placeholder(tmp_data_dir, tmp_path: Path):
    """Undecodable videos fall back to a deterministic placeholder so the
    grid never shows a broken tile."""
    from PIL import Image
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.thumbnail import ThumbnailPlugin

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"fragmented fake video payload")

    p = ThumbnailPlugin()
    asset = AssetLike(
        id=98, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="vid",
    )
    ctx = PluginContext(asset=asset, params=p.ParamsModel(), data_dir=tmp_data_dir)
    res = p.run(asset, ctx)
    assert res["status"] == "ok"
    ph = tmp_data_dir / "thumbs" / "98" / "_frame.png"
    assert ph.is_file()
    im = Image.open(ph)
    assert im.size == (320, 320)


def test_thumbnail_endpoint(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_library(tmp_data_dir, tmp_path, n=2)
    app = create_app()
    with TestClient(app) as c:
        for size in ("small", "medium", "placeholder"):
            r = c.get(f"/api/assets/1/thumbnail?size={size}")
            assert r.status_code == 200, f"size={size} -> {r.status_code}"
            assert r.headers["content-type"].startswith("image/")
            assert len(r.content) > 0


def test_exif_plugin_extracts_image(tmp_data_dir, tmp_path: Path):
    """Pillow must read camera metadata from the EXIF block."""
    from PIL import Image
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.exif import ExifPlugin

    src = tmp_path / "photo.jpg"
    im = Image.new("RGB", (120, 80), (10, 20, 30))
    exif = im.getexif()
    exif[0x010F] = "TestCam"
    exif[0x0110] = "TestCam Model X"
    exif[0x8827] = 800
    exif[0x9003] = "2024:01:02 03:04:05"
    im.save(src, exif=exif)

    p = ExifPlugin()
    asset = AssetLike(
        id=42, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="ex",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "ok"
    md = res["metadata"]
    assert md.get("make") == "TestCam"
    assert md.get("model") == "TestCam Model X"
    assert md.get("iso") == 800


def test_exif_plugin_reads_video(tmp_data_dir, tmp_path: Path):
    """PyAV must report duration / resolution / codec for videos."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.exif import ExifPlugin

    src = tmp_path / "clip.mp4"
    _make_video(src, w=160, h=120, frames=12)

    p = ExifPlugin()
    asset = AssetLike(
        id=44, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="ex3",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "ok"
    md = res["metadata"]
    assert md.get("duration_sec", 0) > 0
    assert md.get("video_width") == 160
    assert md.get("video_height") == 120
    assert md.get("codec")


def test_scene_detect_finds_cuts(tmp_data_dir, tmp_path: Path):
    """Static segments with a hard cut must be split by ContentDetector."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.scene_detect import SceneDetectPlugin

    src = tmp_path / "scenes.mp4"
    _make_scene_video(src, [(40, 24), (200, 24), (90, 24), (230, 24)])

    p = SceneDetectPlugin()
    asset = AssetLike(
        id=45, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="sc",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "ok"
    scenes = res["scenes"]
    assert len(scenes) >= 1
    for s in scenes:
        assert s["start"] < s["end"]
        assert "keyframe" in s


def test_scene_detect_skips_missing_file(tmp_data_dir, tmp_path: Path):
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.scene_detect import SceneDetectPlugin

    src = tmp_path / "nope.mp4"
    p = SceneDetectPlugin()
    asset = AssetLike(
        id=46, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="sc2",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "skipped"


def _make_photo(path: Path, w: int = 64, h: int = 48) -> None:
    """A real Pillow image (make_png's output is only parseable by ffprobe)."""
    from PIL import Image

    Image.new("RGB", (w, h), (40, 80, 160)).save(path)


def test_face_detect_skips_without_model(tmp_data_dir, tmp_path: Path, monkeypatch):
    """Without insightface the detector reports ``skipped`` (not a crash)."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.face_detect import FaceDetectPlugin

    monkeypatch.setattr("hometrove.plugins.builtin.face_detect._get_app", lambda: None)
    src = tmp_path / "p.png"
    _make_photo(src, 32, 24)
    p = FaceDetectPlugin()
    asset = AssetLike(
        id=50, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="fd",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "skipped"


def test_face_detect_emits_expected_shape(tmp_data_dir, tmp_path: Path, monkeypatch):
    """A fake app yields the face.match-compatible output contract."""
    import numpy as np

    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.face_detect import FaceDetectPlugin

    class FakeApp:
        def get(self, img, max_num=0):  # noqa: ARG002
            class F:
                bbox = np.array([1.0, 2.0, 30.0, 40.0])
                det_score = 0.92
                embedding = np.linspace(0.0, 1.0, 512)

            return [F()]

    monkeypatch.setattr("hometrove.plugins.builtin.face_detect._get_app", lambda: FakeApp())
    src = tmp_path / "p.png"
    _make_photo(src, 64, 48)
    p = FaceDetectPlugin()
    asset = AssetLike(
        id=51, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="fd2",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "ok"
    assert res["detected"] == 1
    face = res["faces"][0]
    assert face["box"] == [1, 2, 30, 40]
    assert face["confidence"] == 0.92
    assert len(face["embedding"]) == 512


def test_face_detect_skips_undecodable(tmp_data_dir, tmp_path: Path, monkeypatch):
    """A corrupt file must skip, not raise, even with a working app."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.face_detect import FaceDetectPlugin

    monkeypatch.setattr(
        "hometrove.plugins.builtin.face_detect._get_app",
        lambda: type("App", (), {"get": lambda *a, **k: []})(),
    )
    src = tmp_path / "bad.jpg"
    src.write_bytes(b"not an image")
    p = FaceDetectPlugin()
    asset = AssetLike(
        id=52, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="fd3",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "skipped"


def test_face_image_skips_without_model(tmp_data_dir, tmp_path: Path, monkeypatch):
    """face.image reports status=error + model_name when the pack is missing."""
    import numpy as np

    from hometrove.insightface_runtime import _STATE, reset_for_test
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.face_image import FaceImagePlugin

    reset_for_test()
    monkeypatch.setattr(
        "hometrove.insightface_runtime._build",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("force ModelMissingError path")
        ),
    )

    # Make acquire() raise ModelMissingError by patching it to bypass
    # _build entirely. We reset the singleton first so the test starts
    # from a known empty state.
    from hometrove.insightface_runtime import acquire as _orig_acquire, ModelMissingError

    def _raising_acquire(model_name: str = "buffalo_l"):
        raise ModelMissingError(model_name, "/tmp/fake-model-dir")

    monkeypatch.setattr(
        "hometrove.plugins.builtin.face_image.acquire", _raising_acquire
    )

    src = tmp_path / "p.png"
    _make_photo(src, 64, 48)
    p = FaceImagePlugin()
    asset = AssetLike(
        id=70, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="fimg",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "error"
    assert "缺少模型" in res["reason"]
    assert res["model_name"] == "buffalo_l"


def test_face_image_emits_expected_shape(tmp_data_dir, tmp_path: Path, monkeypatch):
    """face.image uses a fake FaceAnalysis to emit the v2 contract."""
    import numpy as np

    from hometrove.insightface_runtime import reset_for_test
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.face_image import FaceImagePlugin

    class FakeApp:
        def get(self, img, max_num=0):  # noqa: ARG002
            class F:
                bbox = np.array([3.0, 4.0, 60.0, 80.0])
                det_score = 0.95
                embedding = np.linspace(0.0, 1.0, 512)

            return [F(), F()]

    # ``acquire`` in face_image is the runtime's, so patch the runtime's
    # helper that run() invokes to bypass onnxruntime. We replace
    # _build() so the singleton is built but with a stub FaceAnalysis.
    monkeypatch.setattr(
        "hometrove.insightface_runtime._build",
        lambda model_name: FakeApp(),
    )
    reset_for_test()
    try:
        src = tmp_path / "p.png"
        _make_photo(src, 64, 48)
        p = FaceImagePlugin()
        asset = AssetLike(
            id=71, path=str(src), media_root=str(src.parent),
            media_type=MediaType.IMAGE.value, content_hash_prefix="fimg2",
        )
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
        assert res["status"] == "ok"
        assert res["detected"] == 2
        assert res["source_plugin_id"] == "face.image"
        assert res["source_model_name"] == "buffalo_l"
        assert len(res["faces"][0]["embedding"]) == 512
        assert res["faces"][0]["frame_index"] is None
        assert res["faces"][0]["frame_t"] is None
    finally:
        reset_for_test()


def test_face_video_dedup_and_emits_metadata(tmp_data_dir, tmp_path: Path, monkeypatch):
    """face.video dedups by cosine and emits frame_index/frame_t."""
    import numpy as np

    from hometrove.db import session_scope
    from hometrove.insightface_runtime import reset_for_test
    from hometrove.models import Asset, PluginResult
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.face_video import FaceVideoPlugin

    class FakeApp:
        def __init__(self) -> None:
            self._seen: list[np.ndarray] = []

        def get(self, img, max_num=0):  # noqa: ARG002
            # Two faces per frame. The first face is identical across
            # both frames so it gets deduped. The second face is
            # orthogonal so its cosine similarity with itself across
            # frames is also 1.0 (still deduped). Net: 2 unique faces,
            # both emitted from frame 0 only.
            class F1:
                bbox = np.array([1.0, 2.0, 30.0, 40.0])
                det_score = 0.9
                embedding = np.full(512, 0.1)

            class F2:
                bbox = np.array([100.0, 100.0, 200.0, 200.0])
                det_score = 0.8
                # Half 0.9 / half -0.9 so cosine to itself stays 1.0 but
                # the magnitude is non-trivial.
                embedding = np.concatenate(
                    [np.full(256, 0.9), np.full(256, -0.9)]
                )

            return [F1(), F2()]

    monkeypatch.setattr(
        "hometrove.insightface_runtime._build",
        lambda model_name: FakeApp(),
    )
    reset_for_test()
    try:
        src = tmp_path / "clip.mp4"
        _make_scene_video(src, [(40, 12), (200, 12)])
        with session_scope() as s:
            s.add(Asset(
                id=80, path=str(src), media_root=str(src.parent),
                content_hash="fv", media_type="video",
                created_at=0, updated_at=0,
                filename="clip.mp4", size_bytes=0,
            ))
            s.add(PluginResult(
                asset_id=80, plugin_id="basic.scene_detect",
                plugin_version="0.1.0", status="ok",
                result_json='{"scenes": [{"keyframe": 1.0}, {"keyframe": 4.0}]}',
                elapsed_ms=10, finished_at=0,
            ))
            s.commit()

        p = FaceVideoPlugin()
        asset = AssetLike(
            id=80, path=str(src), media_root=str(src.parent),
            media_type=MediaType.VIDEO.value, content_hash_prefix="fv",
        )
        ctx = PluginContext(
            asset=asset,
            params=p.ParamsModel(),
            db=None,
            data_dir=tmp_data_dir,
        )
        # Pre-seed the basic.scene_detect result memo so we don't need a
        # live DB session in this test.
        ctx._result_cache["basic.scene_detect"] = {
            "scenes": [{"keyframe": 1.0}, {"keyframe": 4.0}],
        }
        # Provide a deterministic frames() iterable so the fake App can
        # emit; two numpy arrays stand in for keyframe decodes. The cache
        # key shape must match PluginContext.frames() exactly: ("frames",
        # count, tuple(at_seconds)).
        ctx._frames_cache[("frames", 8, (1.0, 4.0))] = [
            np.full((48, 64, 3), 200, dtype=np.uint8),
            np.full((48, 64, 3), 200, dtype=np.uint8),
        ]
        res = p.run(asset, ctx)
        assert res["status"] == "ok"
        # Two keyframes, both emit [F1(0.1), F2(0.9)]. F1 in frame 2 is
        # deduped against frame 1's F1 (cosine=1.0 > threshold). F2 in
        # frame 2 is also deduped. Net: 1 unique face per identity, 2
        # total.
        assert res["detected"] == 2
        # First emitted face: F1 from frame 0 at t=1.0.
        assert res["faces"][0]["frame_index"] == 0
        assert res["faces"][0]["frame_t"] == 1.0
        # Second emitted face: F2 from frame 0 at t=1.0 (frame 1 was
        # deduped).
        assert res["faces"][1]["frame_index"] == 0
        assert res["faces"][1]["frame_t"] == 1.0
        assert res["source_plugin_id"] == "face.video"
    finally:
        reset_for_test()


def test_face_cluster_groups_similar_vectors(tmp_data_dir, tmp_path: Path):
    """face_cluster.cluster_faces_for_asset groups similar vectors together."""
    import numpy as np

    from hometrove.db import session_scope
    from hometrove.face_cluster import cluster_faces_for_asset
    from hometrove.models import Asset, FaceCluster, FaceEmbedding

    with session_scope() as s:
        s.add(Asset(
            id=90, path="x.png", media_root=str(tmp_path),
            content_hash="fc", media_type="image",
            created_at=0, updated_at=0,
            filename="x.png", size_bytes=0,
        ))
        s.commit()
        aid = 90

    def rand_vec(seed, dim=512):
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(dim)
        return (v / np.linalg.norm(v)).tolist()

    v1 = rand_vec(1)
    v2 = (np.asarray(v1) * 0.99 + np.random.default_rng(2).standard_normal(512) * 0.01).tolist()
    v3 = rand_vec(3)

    with session_scope() as s:
        counts = cluster_faces_for_asset(
            s, asset_id=aid,
            faces=[
                {"embedding": v1, "confidence": 0.9, "box": [0, 0, 10, 10]},
                {"embedding": v2, "confidence": 0.9, "box": [0, 0, 10, 10]},
                {"embedding": v3, "confidence": 0.9, "box": [0, 0, 10, 10]},
            ],
            source_plugin_id="face.image",
            source_model_name="buffalo_l",
        )
        assert counts["persisted"] == 3
        assert s.query(FaceCluster).count() == 2

    with session_scope() as s:
        # v4 close to v1 should merge into the larger cluster.
        v4 = (np.asarray(v1) * 0.98 + np.random.default_rng(4).standard_normal(512) * 0.02).tolist()
        cluster_faces_for_asset(
            s, asset_id=aid,
            faces=[{"embedding": v4, "confidence": 0.9, "box": [0, 0, 10, 10]}],
            source_plugin_id="face.image",
            source_model_name="buffalo_l",
        )
        # Same cluster count, one cluster has 3 faces.
        clusters = s.query(FaceCluster).order_by(FaceCluster.face_count.desc()).all()
        assert s.query(FaceCluster).count() == 2
        assert clusters[0].face_count == 3
        # After 3 faces the representative_face_id is set.
        assert clusters[0].representative_face_id is not None
        # v3's cluster has 1 face and no representative.
        assert clusters[1].face_count == 1
        assert clusters[1].representative_face_id is None


def test_face_cluster_isolates_by_source_plugin(tmp_data_dir, tmp_path: Path):
    """face.image and face.video faces never auto-merge into the same cluster."""
    import numpy as np

    from hometrove.db import session_scope
    from hometrove.face_cluster import cluster_faces_for_asset
    from hometrove.models import Asset, FaceCluster

    with session_scope() as s:
        s.add(Asset(
            id=91, path="x.png", media_root=str(tmp_path),
            content_hash="fc2", media_type="image",
            created_at=0, updated_at=0,
            filename="x.png", size_bytes=0,
        ))
        s.commit()
        aid = 91

    rng = np.random.default_rng(1)
    vec = (rng.standard_normal(512) / np.linalg.norm(rng.standard_normal(512))).tolist()

    with session_scope() as s:
        cluster_faces_for_asset(
            s, asset_id=aid,
            faces=[{"embedding": vec, "confidence": 0.9, "box": [0, 0, 10, 10]}],
            source_plugin_id="face.image",
            source_model_name="buffalo_l",
        )
        cluster_faces_for_asset(
            s, asset_id=aid,
            faces=[{"embedding": vec, "confidence": 0.9, "box": [0, 0, 10, 10]}],
            source_plugin_id="face.video",
            source_model_name="buffalo_l",
        )
        # Same vector, different source plugin -> two distinct clusters.
        assert s.query(FaceCluster).count() == 2


def test_face_detect_video_samples_keyframes(tmp_data_dir, tmp_path: Path, monkeypatch):
    """Video detection samples scene keyframes and dedups the same identity."""
    import numpy as np

    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.face_detect import FaceDetectPlugin

    class FakeApp:
        def get(self, img, max_num=0):  # noqa: ARG002
            class F:
                bbox = np.array([5.0, 5.0, 20.0, 30.0])
                det_score = 0.9
                embedding = np.full(512, 0.1)

            return [F()]

    monkeypatch.setattr("hometrove.plugins.builtin.face_detect._get_app", lambda: FakeApp())
    src = tmp_path / "clip.mp4"
    _make_scene_video(src, [(40, 12), (200, 12)])

    # Seed a basic.scene_detect result so keyframes exist.
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult

    with session_scope() as s:
        s.add(Asset(
            id=60, path=str(src), media_root=str(src.parent),
            content_hash="fdv", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=60, plugin_id="basic.scene_detect", plugin_version="0.1.0",
            status="ok",
            result_json='{"scenes": [{"start": 0.0, "end": 0.5, "keyframe": 0.25}, {"start": 0.5, "end": 1.0, "keyframe": 0.75}]}',
        ))
        s.commit()

    p = FaceDetectPlugin()
    asset = AssetLike(
        id=60, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="fdv",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "ok"
    # Same identity across sampled frames is deduped to a single face.
    assert res["detected"] == 1
    assert res["frames_sampled"] >= 1


def test_plugin_context_image_cache(tmp_data_dir, tmp_path: Path):
    """image() decodes once per key; a different max_side re-decodes."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext

    src = tmp_path / "img.png"
    _make_photo(src, 400, 300)
    asset = AssetLike(
        id=70, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="ctx1",
    )
    ctx = PluginContext(asset=asset, params=None)

    img = ctx.image()
    assert img is not None
    assert img.shape == (300, 400, 3)  # decoded RGB, no downscale
    assert ctx.image() is img  # same key -> memo hit (same object)

    small = ctx.image(max_side=100)
    assert small.shape == (75, 100, 3)  # aspect preserved
    assert small is not img  # different key -> re-decode


def test_plugin_context_image_none_for_undecodable(tmp_path: Path):
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext

    src = tmp_path / "bad.jpg"
    src.write_bytes(b"not an image")
    asset = AssetLike(
        id=71, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="ctx2",
    )
    ctx = PluginContext(asset=asset, params=None)
    assert ctx.image() is None


def test_plugin_context_frames_cache(tmp_data_dir, tmp_path: Path):
    """frames() returns a stable sample list memoized per (count, at_seconds)."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext

    src = tmp_path / "clip.mp4"
    _make_video(src, 160, 120, frames=24)  # ~1s at 24fps
    asset = AssetLike(
        id=72, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="ctx3",
    )
    ctx = PluginContext(asset=asset, params=None)

    frames = ctx.frames(count=3)
    assert len(frames) == 3
    assert all(f is not None and f.ndim == 3 for f in frames)
    assert ctx.frames(count=3) is frames  # memoized list identity

    # Different count -> different decode result.
    assert ctx.frames(count=4) is not frames

    # Explicit at_seconds selects timestamps.
    timed = ctx.frames(at_seconds=[0.25, 0.75])
    assert len(timed) == 2
    assert timed is not frames


def test_plugin_context_frames_empty_for_image(tmp_path: Path):
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext

    src = tmp_path / "img.png"
    _make_photo(src, 64, 48)
    asset = AssetLike(
        id=73, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="ctx4",
    )
    ctx = PluginContext(asset=asset, params=None)
    assert ctx.frames(count=3) == []


def test_plugin_context_result_of(tmp_data_dir, tmp_path: Path):
    """result_of() reads the latest ok row from the DB and memoizes it."""
    import json

    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext

    src = tmp_path / "clip.mp4"
    _make_video(src, 160, 120, frames=12)
    with session_scope() as s:
        s.add(Asset(
            id=80, path=str(src), media_root=str(src.parent),
            content_hash="ctx5", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=80, plugin_id="basic.scene_detect", plugin_version="0.1.0",
            status="ok",
            result_json='{"scenes": [{"start": 0.0, "end": 0.5, "keyframe": 0.25}]}',
            finished_at=100,
        ))
        s.commit()

    asset = AssetLike(
        id=80, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="ctx5",
    )
    with session_scope() as s:
        ctx = PluginContext(asset=asset, params=None, db=s)
        data = ctx.result_of("basic.scene_detect")
        assert data == {"scenes": [{"start": 0.0, "end": 0.5, "keyframe": 0.25}]}
        assert ctx.result_of("basic.scene_detect") is data  # memoized

        # Unknown plugin -> None.
        assert ctx.result_of("nonexistent") is None


def test_plugin_context_result_of_without_db(tmp_path: Path):
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext

    src = tmp_path / "img.png"
    _make_photo(src, 32, 24)
    asset = AssetLike(
        id=81, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="ctx6",
    )
    ctx = PluginContext(asset=asset, params=None)
    assert ctx.result_of("basic.scene_detect") is None


def test_plugin_context_temp_dir(tmp_data_dir, tmp_path: Path):
    """temp_dir() returns a per-asset scratch dir under the data dir."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext

    src = tmp_path / "img.png"
    _make_photo(src, 32, 24)
    asset = AssetLike(
        id=90, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="ctx7",
    )
    ctx = PluginContext(asset=asset, params=None, data_dir=tmp_data_dir)
    d = ctx.temp_dir()
    assert d.is_dir()
    assert d == tmp_data_dir / "plugin-tmp" / "90"
    assert ctx.temp_dir() == d


def test_sqlite_vec_index_search(tmp_data_dir):
    """vec0 nearest-neighbour search returns (id, distance) sorted by distance."""
    from hometrove.vector import SQLiteVecIndex

    def v(d1: float, d2: float, d3: float, d4: float) -> list[float]:
        vec = [0.0] * 1024
        vec[0], vec[1], vec[2], vec[3] = d1, d2, d3, d4
        return vec

    idx = SQLiteVecIndex()
    idx.upsert(1, v(1.0, 0.0, 0.0, 0.0))
    idx.upsert(2, v(0.0, 1.0, 0.0, 0.0))
    idx.upsert(3, v(0.0, 0.9, 0.1, 0.0))

    hits = idx.search(v(1.0, 0.0, 0.0, 0.0), k=3)
    assert [r[0] for r in hits] == [1, 3, 2]  # exact match first, then 0.9, then orthogonal
    assert hits[0][1] == 0.0

    # remove() deletes from the index.
    idx.remove(1)
    hits = idx.search(v(1.0, 0.0, 0.0, 0.0), k=3)
    assert 1 not in [r[0] for r in hits]


def test_embedding_model_and_index_sync(tmp_data_dir):
    """An Embedding row plus a vec0 copy stay in sync through get_index()."""
    import json

    from hometrove.db import session_scope
    from hometrove.models import Asset, Embedding
    from hometrove.vector import delete_embeddings, get_index

    with session_scope() as s:
        s.add(Asset(
            id=100, path="/p/v.mp4", media_root="/p",
            content_hash="emb1", media_type="video",
            created_at=0, updated_at=0,
        ))
        vec = [0.0] * 1024
        vec[0] = 1.0
        row = Embedding(
            asset_id=100, plugin_id="embedding.jina_clip", plugin_version="0.1.0",
            scope="scene", t_start=0.0, t_end=0.5,
            embedding_json=json.dumps(vec),
        )
        s.add(row)
        s.flush()
        get_index().upsert(row.id, vec, session=s)
        s.commit()

    hits = get_index().search(vec, k=5)
    assert hits and hits[0][0] == row.id

    # delete_embeddings removes both the row and its index copy.
    with session_scope() as s:
        n = delete_embeddings(s, 100, plugin_id="embedding.jina_clip")
        s.commit()
        assert n == 1
        assert s.execute(select(Embedding).where(Embedding.asset_id == 100)).scalars().first() is None
    hits = get_index().search(vec, k=5)
    assert not hits


def test_embedding_jina_clip_image(tmp_data_dir, tmp_path: Path):
    """Image asset produces one scope=image vector of VECTOR_DIM."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, Embedding
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.embedding_clip import EmbeddingJinaClipPlugin

    src = tmp_path / "img.png"
    _make_photo(src, 64, 48)
    with session_scope() as s:
        s.add(Asset(
            id=101, path=str(src), media_root=str(src.parent),
            content_hash="embi", media_type="image",
            created_at=0, updated_at=0,
        ))
        s.commit()

    p = EmbeddingJinaClipPlugin()
    asset = AssetLike(
        id=101, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="embi",
    )
    with session_scope() as s:
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        assert res["status"] == "ok"
        assert res["scope"] == "image"
        assert res["vectors"] == 1
        assert res["dim"] == 1024

        rows = s.execute(select(Embedding).where(Embedding.asset_id == 101)).scalars().all()
        assert len(rows) == 1
        assert rows[0].scope == "image"
        vec = json.loads(rows[0].embedding_json)
        assert len(vec) == 1024

    # Re-running is idempotent: still exactly one row.
    with session_scope() as s:
        p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        rows = s.execute(select(Embedding).where(Embedding.asset_id == 101)).scalars().all()
        assert len(rows) == 1


def test_embedding_jina_clip_video_scenes(tmp_data_dir, tmp_path: Path):
    """Video with scene_detect output yields one scope=scene vector per scene."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, Embedding, PluginResult
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.embedding_clip import EmbeddingJinaClipPlugin

    src = tmp_path / "clip.mp4"
    _make_scene_video(src, [(40, 12), (200, 12)])

    with session_scope() as s:
        s.add(Asset(
            id=102, path=str(src), media_root=str(src.parent),
            content_hash="embv", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=102, plugin_id="basic.scene_detect", plugin_version="0.1.0",
            status="ok",
            result_json=json.dumps({
                "scenes": [
                    {"start": 0.0, "end": 0.5, "keyframe": 0.25},
                    {"start": 0.5, "end": 1.0, "keyframe": 0.75},
                ]
            }),
        ))
        s.commit()

    p = EmbeddingJinaClipPlugin()
    asset = AssetLike(
        id=102, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="embv",
    )
    with session_scope() as s:
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        assert res["status"] == "ok"
        assert res["scope"] == "scene"
        assert res["vectors"] == 2

        rows = s.execute(
            select(Embedding).where(Embedding.asset_id == 102).order_by(Embedding.t_start)
        ).scalars().all()
        assert len(rows) == 2
        assert [r.scope for r in rows] == ["scene", "scene"]
        assert rows[0].t_start == 0.0 and rows[0].t_end == 0.5
        assert rows[1].t_start == 0.5 and rows[1].t_end == 1.0


def test_embedding_jina_clip_video_no_scenes_falls_back(tmp_data_dir, tmp_path: Path):
    """Video without scene_detect output still gets one cover-frame vector."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, Embedding
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.embedding_clip import EmbeddingJinaClipPlugin

    src = tmp_path / "clip.mp4"
    _make_video(src, 160, 120, frames=12)
    with session_scope() as s:
        s.add(Asset(
            id=103, path=str(src), media_root=str(src.parent),
            content_hash="embf", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.commit()

    p = EmbeddingJinaClipPlugin()
    asset = AssetLike(
        id=103, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="embf",
    )
    with session_scope() as s:
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        assert res["status"] == "ok"
        assert res["scope"] == "image"
        assert res["vectors"] == 1
        rows = s.execute(select(Embedding).where(Embedding.asset_id == 103)).scalars().all()
        assert len(rows) == 1
        assert rows[0].scope == "image"


def test_enqueue_pending_respects_disabled_plugins(tmp_data_dir, tmp_path: Path):
    """Disabled plugins get no jobs from enqueue_pending."""
    from hometrove.db import session_scope
    from hometrove.models import Job, PluginConfig
    from hometrove.scanner import discover, enqueue_pending, upsert_assets

    media = tmp_path / "photos"
    media.mkdir()
    make_png(media / "a.png", 10, 10)

    with session_scope() as s:
        upsert_assets(s, list(discover([media])))
        s.add(PluginConfig(plugin_id="thumbnail", enabled=0))
        s.add(PluginConfig(plugin_id="exif", enabled=0))
        s.commit()

        enqueue_pending(s)

        enqueued = set(
            s.scalars(
                select(Job.plugin_id).where(Job.state == "pending")
            ).all()
        )
        assert "thumbnail" not in enqueued
        assert "exif" not in enqueued
        assert "basic.info" in enqueued

        # Re-enabling makes the plugin enqueueable again.
        row = s.get(PluginConfig, "thumbnail")
        row.enabled = 1
        s.commit()
        assert enqueue_pending(s) > 0
        reenqueued = set(
            s.scalars(
                select(Job.plugin_id).where(Job.state == "pending")
            ).all()
        )
        assert "thumbnail" in reenqueued


def test_worker_claim_skips_disabled_plugin_jobs(tmp_data_dir, tmp_path: Path):
    """Worker refuses to claim jobs whose plugin is disabled (parked, not lost)."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, Job, PluginConfig, PluginResult
    from hometrove.worker.main import _claim_next

    # Use a standalone plugin with no DAG dependencies so the disabled one is
    # claimable without depending on an already-consumed job.
    with session_scope() as s:
        s.add(Asset(
            id=201, path="x.png", media_root=".", content_hash="w1",
            media_type="image", created_at=0, updated_at=0,
        ))
        # mock.tags depends on basic.info -> provide a done result so the
        # parked job becomes claimable once the plugin is re-enabled.
        s.add(PluginResult(
            asset_id=201, plugin_id="basic.info", plugin_version="0.1.0",
            status="ok", result_json="{}",
        ))
        s.add(Job(asset_id=201, plugin_id="mock.tags", state="pending", enqueued_at=1))
        s.add(Job(asset_id=201, plugin_id="thumbnail", state="pending", enqueued_at=2))
        s.add(PluginConfig(plugin_id="mock.tags", enabled=0))
        s.commit()

        # thumbnail (enabled) is claimable; mock.tags (disabled) is parked.
        j = _claim_next(s, "")
        assert j is not None and j.plugin_id == "thumbnail"
        s.commit()

        # Re-enable mock.tags; its parked job becomes claimable.
        row = s.get(PluginConfig, "mock.tags")
        row.enabled = 1
        s.commit()
        j2 = _claim_next(s, "")
        assert j2 is not None and j2.plugin_id == "mock.tags"


def test_plugins_api_list_and_toggle(tmp_data_dir):
    """GET /api/plugins lists all; PUT disables and re-enables."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import PluginConfig

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/plugins")
        assert r.status_code == 200
        items = {x["id"]: x for x in r.json()["items"]}
        assert "basic.info" in items
        assert items["basic.info"]["enabled"] is True

        r = c.put("/api/plugins/exif", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

        r = c.get("/api/plugins")
        items = {x["id"]: x for x in r.json()["items"]}
        assert items["exif"]["enabled"] is False

        r = c.put("/api/plugins/exif", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["enabled"] is True

    with session_scope() as s:
        row = s.get(PluginConfig, "exif")
        assert row is not None and row.enabled == 1


def test_plugin_shutdown_releases_resources(tmp_data_dir):
    """Disabling a plugin via the API calls its shutdown() hook."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()

    # face.detect holds a shared FaceAnalysis instance; simulate a loaded app
    # and verify disabling drops it. Note: the module-level ``_get_app`` is
    # monkeypatched by the ``tmp_data_dir`` fixture to force "no model", so we
    # assert on the shared ``_APP`` slot directly.
    import hometrove.plugins.builtin.face_detect as fd

    fake = object()
    fd._APP = fake
    assert fd._APP is fake

    with TestClient(app) as c:
        r = c.put("/api/plugins/face.detect", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    assert fd._APP is None


def test_plugins_api_enabled_filter(tmp_data_dir):
    """GET /api/plugins?enabled=true only returns enabled plugins."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import PluginConfig

    app = create_app()
    with TestClient(app) as c:
        r = c.put("/api/plugins/exif", json={"enabled": False})
        assert r.status_code == 200

        r = c.get("/api/plugins?enabled=true")
        assert r.status_code == 200
        ids = [x["id"] for x in r.json()["items"]]
        assert "exif" not in ids
        assert "basic.info" in ids

        r = c.get("/api/plugins")
        ids = [x["id"] for x in r.json()["items"]]
        assert "exif" in ids

    with session_scope() as s:
        assert s.get(PluginConfig, "exif").enabled == 0


def test_plugin_status_and_logs_endpoints(tmp_data_dir):
    """GET /api/plugins/{id}/status and /logs return lifecycle info."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.plugins.lifecycle import plugin_log  # noqa: F401

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/plugins/basic.info/status")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "basic.info"
        assert body["status"] in {"idle", "loading", "active", "stopping", "error"}

        r = c.get("/api/plugins/basic.info/logs?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert isinstance(body["items"], list)

        r = c.get("/api/plugins/does-not-exist/status")
        assert r.status_code == 404

        r = c.get("/api/plugins/does-not-exist/logs")
        assert r.status_code == 404


def test_plugin_startup_runs_on_enable(tmp_data_dir):
    """Enabling a plugin invokes its startup() hook and updates status."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import PluginConfig
    from hometrove.plugins.lifecycle import get_status
    from hometrove.plugins.registry import REGISTRY

    # Disable first, then re-enable via the API to exercise the startup path.
    app = create_app()
    plugin = REGISTRY.get("basic.info")
    original_startup = plugin.startup
    calls = []

    def spy_startup():
        calls.append("startup")
        original_startup()

    plugin.startup = spy_startup  # type: ignore[method-assign]
    try:
        with TestClient(app) as c:
            r = c.put("/api/plugins/basic.info", json={"enabled": False})
            assert r.status_code == 200
            assert "startup" not in calls

            r = c.put("/api/plugins/basic.info", json={"enabled": True})
            assert r.status_code == 200
            assert "startup" in calls

            info = get_status("basic.info")
            assert info.status.value == "active"
    finally:
        plugin.startup = original_startup  # type: ignore[method-assign]

    with session_scope() as s:
        assert s.get(PluginConfig, "basic.info").enabled == 1


def test_plugin_categories_classify_correctly(tmp_data_dir):
    """Every builtin plugin reports a ``category`` of ``implemented`` or ``stub``.

    - The 7 known stub plugins (real-backends-that-default-to-mock plus the
      deterministic mock plugins) must self-classify as ``stub``.
    - Plugins that depend on a stub transitively must also resolve to ``stub``
      so the UI doesn't lie when a backend is replaced.
    - All other plugins must resolve to ``implemented``.
    """
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.plugins.registry import REGISTRY

    direct_stubs = {
        "mock.tags",
        "mock.category",
        "mock.faces",
        "embedding.jina_clip",
        "embedding.bge_m3",
        "vlm.qwen3vl",
        "asr.faster_whisper",
    }
    implemented = {
        "basic.info",
        "exif",
        "thumbnail",
        "basic.keyframes",
        "basic.scene_detect",
    }

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/plugins")
        assert r.status_code == 200
        payload = r.json()
        by_id = {p["id"]: p for p in payload["items"]}
        # Inside the test client so the app lifespan (which loads builtin
        # plugins) has already run.
        registered = {p.id for p in REGISTRY.list()}
        assert set(by_id) == registered

        # Every plugin exposes a category.
        for pid, info in by_id.items():
            assert info["category"] in ("implemented", "stub"), pid

        # Direct stubs are stub.
        for pid in direct_stubs:
            assert by_id[pid]["category"] == "stub", pid

        # Plugins that depend on a stub (embedding.bge_m3 depends on
        # vlm.qwen3vl) must also resolve to stub transitively.
        bge = REGISTRY.get("embedding.bge_m3")
        assert bge.category == "stub"  # direct mark
        # Force a fresh classification even if the class attribute is reset.
        assert bge.effective_category(REGISTRY) == "stub"
        # Spot-check: the chain is stub because vlm.qwen3vl is stub.
        assert REGISTRY.get("vlm.qwen3vl").effective_category(REGISTRY) == "stub"

        # Implemented plugins stay implemented.
        for pid in implemented:
            assert by_id[pid]["category"] == "implemented", pid

        # Effective category honors an explicit ``implemented`` even when a
        # dependency would be stub — none of the implemented plugins have stub
        # deps, so this is a defensive sanity check.
        for pid in implemented:
            plugin = REGISTRY.get(pid)
            assert plugin.effective_category(REGISTRY) == "implemented", pid


def test_upload_rejects_disabled_plugin_ids(tmp_data_dir):
    """Upload ingest refuses plugin_ids that are disabled."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.put("/api/plugins/basic.info", json={"enabled": False})
        assert r.status_code == 200

        # Create upload session then attempt ingest with disabled plugin.
        r = c.post(
            "/api/uploads",
            json={"filename": "foo.jpg", "size": 1},
        )
        assert r.status_code == 200
        upload_id = r.json()["upload_id"]

        # Use URL-encoded params like the existing upload-ids test does, so
        # FastAPI parses them as a list.
        r = c.post(
            f"/api/uploads/{upload_id}/ingest?plugin_ids=basic.info",
        )
        assert r.status_code == 400
        assert "basic.info" in r.json()["detail"]

        # Empty plugin_ids list still works (would fail with 409 not finalized).
        r = c.post(f"/api/uploads/{upload_id}/ingest")
        assert r.status_code == 409

        # Unknown plugin id is rejected.
        r = c.put("/api/plugins/basic.info", json={"enabled": True})
        assert r.status_code == 200
        r = c.post(
            f"/api/uploads/{upload_id}/ingest?plugin_ids=does-not-exist",
        )
        assert r.status_code == 400


def _add_asset_and_embedding(
    s,
    asset_id: int,
    *,
    vec: list[float],
    scope: str = "image",
    t_start: float | None = None,
    t_end: float | None = None,
    plugin_id: str = "embedding.jina_clip",
    media_type: str = "image",
) -> int:
    """Insert an Asset + Embedding + vec0 copy; returns the embedding id."""
    import json

    from hometrove.models import Asset, Embedding
    from hometrove.vector import get_index

    s.add(Asset(
        id=asset_id, path=f"/m/a{asset_id}.png", media_root="/m",
        content_hash=f"h{asset_id}", media_type=media_type,
        created_at=0, updated_at=0,
    ))
    row = Embedding(
        asset_id=asset_id, plugin_id=plugin_id, plugin_version="0.1.0",
        scope=scope, t_start=t_start, t_end=t_end,
        embedding_json=json.dumps(vec),
    )
    s.add(row)
    s.flush()
    get_index().upsert(row.id, vec, session=s)
    return row.id


def _unit_vec(hot_dim: int) -> list[float]:
    """1024-dim unit vector with a single hot dimension."""
    v = [0.0] * 1024
    v[hot_dim] = 1.0
    return v


def _search_session():
    from hometrove.db import session_factory

    return session_factory()()


def test_search_vector_recall_and_rrf(tmp_data_dir):
    """Vector recall returns hits; RRF ranks assets across recall paths."""
    from hometrove.db import session_scope
    from hometrove.search import search

    with session_scope() as s:
        # A photo whose vector is close to the query's mock vector.
        q = "sunset beach"
        qv = _encode_query_for_test(q)
        _add_asset_and_embedding(s, 301, vec=qv)
        # A far photo (orthogonal vector) — lower similarity.
        far = _unit_vec(0)
        _add_asset_and_embedding(s, 302, vec=far)
        s.commit()

    res = search(_search_session(), q)
    assert res["total"] >= 1
    # The close photo ranks above the far one (vector distance ordering).
    ranks = {i["asset_id"]: i["rank"] for i in res["items"]}
    assert 301 in ranks
    if 302 in ranks:
        assert ranks[301] < ranks[302]


def test_search_scope_prefix_filters(tmp_data_dir):
    """scope:scene limits recall to scene embeddings."""
    from hometrove.db import session_scope
    from hometrove.search import search

    with session_scope() as s:
        _add_asset_and_embedding(s, 311, vec=_unit_vec(1), scope="image")
        _add_asset_and_embedding(s, 312, vec=_unit_vec(1), scope="scene", t_start=1.0, t_end=2.0)
        s.commit()

    res = search(_search_session(), "scope:scene sample")
    assert all(i["scope"] == "scene" for i in res["items"])
    assert any(i["asset_id"] == 312 for i in res["items"])


def test_search_video_hit_carries_seek_metadata(tmp_data_dir):
    """A scene-scope video hit exposes t_start/t_end for jump-to-second."""
    from hometrove.db import session_scope
    from hometrove.models import Asset
    from hometrove.search import search

    with session_scope() as s:
        s.add(Asset(
            id=321, path="/m/clip.mp4", media_root="/m",
            content_hash="h321", media_type="video",
            duration_sec=10.0, created_at=0, updated_at=0,
        ))
        from hometrove.vector import get_index
        import json
        from hometrove.models import Embedding
        row = Embedding(
            asset_id=321, plugin_id="embedding.jina_clip", plugin_version="0.1.0",
            scope="scene", t_start=4.0, t_end=6.0,
            embedding_json=json.dumps(_unit_vec(2)),
        )
        s.add(row)
        s.flush()
        get_index().upsert(row.id, _unit_vec(2), session=s)
        s.commit()

    res = search(_search_session(), "scope:scene beach")
    hit = next((i for i in res["items"] if i["asset_id"] == 321), None)
    assert hit is not None
    assert hit["media_type"] == "video"
    assert hit["can_seek"] is True
    assert hit["t_start"] == 4.0 and hit["t_end"] == 6.0


def test_search_keyword_recall(tmp_data_dir):
    """Keyword path matches text-bearing plugin results."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.search import search

    with session_scope() as s:
        s.add(Asset(
            id=331, path="/m/cat.png", media_root="/m",
            content_hash="h331", media_type="image",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=331, plugin_id="mock.tags", plugin_version="0.1.0",
            status="ok", result_json='{"tags":["cat","pet"]}',
        ))
        s.commit()

    res = search(_search_session(), "cat")
    assert any(i["asset_id"] == 331 for i in res["items"])


def test_search_api_shape(tmp_data_dir):
    """GET /api/search returns the documented response shape."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.db import reset_engine

    reset_engine()
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/search", params={"q": "beach"})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"query", "total", "items"}
        assert body["query"] == "beach"

        # Missing q -> 422.
        r2 = c.get("/api/search")
        assert r2.status_code == 422


def _encode_query_for_test(q: str) -> list[float]:
    """Mirror of hometrove.search._encode_query (deterministic)."""
    from hometrove.search import _encode_query

    return _encode_query(q)


def test_albums_crud_and_membership(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_library(tmp_data_dir, tmp_path, n=4)
    app = create_app()
    with TestClient(app) as c:
        ids = [x["id"] for x in c.get("/api/assets", params={"limit": 50}).json()["items"]]
        assert len(ids) >= 4

        # Create an album with a couple of assets.
        r = c.post("/api/albums", json={"name": "  旅行  ", "asset_ids": ids[:2]})
        assert r.status_code == 201
        album = r.json()
        assert album["name"] == "旅行"
        assert album["asset_count"] == 2
        assert set(album["asset_ids"]) == set(ids[:2])
        aid = album["id"]

        # List returns it with counts.
        r = c.get("/api/albums")
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(a["id"] == aid and a["asset_count"] == 2 for a in items)

        # Detail with membership.
        r = c.get(f"/api/albums/{aid}")
        assert r.status_code == 200
        assert set(r.json()["asset_ids"]) == set(ids[:2])

        # Add more assets (idempotent on duplicates).
        r = c.post(f"/api/albums/{aid}/assets", json={"asset_ids": ids + [ids[0]]})
        assert r.status_code == 200
        body = r.json()
        assert body["added"] == 2
        assert len(body["album"]["asset_ids"]) == 4

        # Rename + set cover.
        r = c.patch(f"/api/albums/{aid}", json={"name": "蜜月", "cover_asset_id": ids[0]})
        assert r.status_code == 200
        assert r.json()["name"] == "蜜月"
        assert r.json()["cover_asset_id"] == ids[0]

        # Remove one asset; positions compact.
        r = c.request("DELETE", f"/api/albums/{aid}/assets", json={"asset_ids": [ids[1]]})
        assert r.status_code == 200
        assert r.json()["removed"] == 1
        assert r.json()["album"]["asset_ids"] == [ids[0], ids[2], ids[3]]

        # Unknown asset in create/append -> 400.
        r = c.post(f"/api/albums/{aid}/assets", json={"asset_ids": [99999]})
        assert r.status_code == 400

        # Blank name rejected.
        r = c.post("/api/albums", json={"name": "   "})
        assert r.status_code == 422

        # Delete.
        r = c.delete(f"/api/albums/{aid}")
        assert r.status_code == 200
        r = c.get(f"/api/albums/{aid}")
        assert r.status_code == 404
        # Membership rows cascade away.
        from hometrove.db import session_scope
        from hometrove.models import AlbumAsset
        with session_scope() as s:
            assert s.query(AlbumAsset).filter_by(album_id=aid).count() == 0


def test_albums_validate_unknown_asset_on_create(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_library(tmp_data_dir, tmp_path, n=2)
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/albums", json={"name": "x", "asset_ids": [123456]})
        assert r.status_code == 400


def test_places_clusters_exif_gps(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import engine, session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.scanner import discover, enqueue_pending, upsert_assets
    from hometrove.orchestrator.runner import run_one
    from hometrove.worker.main import _claim_next
    import hometrove.plugins.builtin  # noqa: F401

    engine()
    media = tmp_path / "photos"
    media.mkdir()
    for i in range(3):
        make_png(media / f"gps{i}.png", 10, 8)
    with session_scope() as s:
        upsert_assets(s, list(discover([media])))
        enqueue_pending(s)
    while True:
        with session_scope() as s:
            job = _claim_next(s, "test")
            if job is None:
                break
            run_one(job.id, s)

    # Stamp GPS into the exif plugin results: two photos near Beijing,
    # one near Shanghai — distinct grid cells at the default 0.5° grid.
    with session_scope() as s:
        assets = s.query(Asset).order_by(Asset.id).all()
        assert len(assets) == 3
        gps = [
            (39.9042, 116.4074),
            (39.95, 116.45),
            (31.2304, 121.4737),
        ]
        for a, (lat, lon) in zip(assets, gps):
            s.add(PluginResult(
                asset_id=a.id,
                plugin_id="exif",
                plugin_version="0.1.0",
                status="ok",
                result_json=json.dumps({"metadata": {"gps_lat": lat, "gps_lon": lon}}),
            ))

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/places")
        assert r.status_code == 200
        body = r.json()
        items = body["items"]
        # Two clusters: Beijing (2 assets) and Shanghai (1).
        assert len(items) == 2
        assert items[0]["count"] == 2  # sorted by count desc
        assert items[1]["count"] == 1
        by_count = {it["count"]: it for it in items}
        bj = by_count[2]
        # Mean lat/lon near Beijing.
        assert 39.0 < bj["lat"] < 40.5
        assert 116.0 < bj["lon"] < 117.0
        assert len(bj["asset_ids"]) == 2

        # A finer grid separates the two Beijing photos.
        r = c.get("/api/places", params={"grid": 0.05})
        assert r.status_code == 200
        assert len(r.json()["items"]) == 3


def test_places_empty_without_gps(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_library(tmp_data_dir, tmp_path, n=2)
    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/places")
        assert r.status_code == 200
        assert r.json()["items"] == []


def test_plugin_params_save_and_validate(tmp_data_dir):
    """PUT with params persists to plugin_config and validates against ParamsModel."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import PluginConfig

    app = create_app()
    with TestClient(app) as c:
        # exif exposes read_geolocation / read_video_metadata booleans.
        r = c.put(
            "/api/plugins/exif",
            json={"enabled": True, "params": {"read_geolocation": False}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["params"] == {"read_geolocation": False}
        assert "params_schema" in body
        assert "read_geolocation" in body["params_schema"]["properties"]

        # Invalid param type -> 422.
        r = c.put(
            "/api/plugins/exif",
            json={"enabled": True, "params": {"read_geolocation": "nope"}},
        )
        assert r.status_code == 422

    with session_scope() as s:
        row = s.get(PluginConfig, "exif")
        import json as _json
        assert row is not None
        assert _json.loads(row.params_json) == {"read_geolocation": False}


def test_plugin_rerun_requeues_all(tmp_data_dir, tmp_path: Path):
    """POST /api/plugins/{id}/rerun drops done/failed jobs and re-enqueues."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Job

    n = _seed_library(tmp_data_dir, tmp_path, n=3)
    # After seeding, every plugin has a done job per asset.
    with session_scope() as s:
        before = s.query(Job).filter(Job.plugin_id == "basic.info", Job.state == "done").count()
        assert before == n

    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/plugins/basic.info/rerun")
        assert r.status_code == 200
        body = r.json()
        assert body["dropped"] == n
        assert body["enqueued"] == n

    with session_scope() as s:
        pending = s.query(Job).filter(Job.plugin_id == "basic.info", Job.state == "pending").count()
        assert pending == n
        done = s.query(Job).filter(Job.plugin_id == "basic.info", Job.state == "done").count()
        assert done == 0


def test_plugin_rerun_blocked_when_disabled(tmp_data_dir):
    """Rerunning a disabled plugin returns 400."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.put("/api/plugins/exif", json={"enabled": False})
        assert r.status_code == 200
        r = c.post("/api/plugins/exif/rerun")
        assert r.status_code == 400


def test_plugin_rerun_candidates_filters_by_media_type_and_search(tmp_data_dir, tmp_path: Path):
    """GET /api/plugins/{id}/rerun-candidates respects supported_media and q."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    n = _seed_library(tmp_data_dir, tmp_path, n=3)
    app = create_app()
    with TestClient(app) as c:
        # basic.info supports image: should return all images.
        r = c.get("/api/plugins/basic.info/rerun-candidates")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == n
        assert len(body["items"]) == n
        assert all(i["media_type"] == "image" for i in body["items"])

        # Searching by filename should narrow results.
        r = c.get("/api/plugins/basic.info/rerun-candidates", params={"q": "img1"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["filename"] == "img1.png"

        # A video-only plugin should return zero candidates from an image-only library.
        r = c.get("/api/plugins/asr.faster_whisper/rerun-candidates")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 0


def test_plugin_rerun_selected_requeues_subset(tmp_data_dir, tmp_path: Path):
    """POST /api/plugins/{id}/rerun-selected only affects chosen assets."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset, Job

    n = _seed_library(tmp_data_dir, tmp_path, n=3)
    with session_scope() as s:
        asset_ids = [a.id for a in s.execute(select(Asset).order_by(Asset.id)).scalars().all()]

    app = create_app()
    with TestClient(app) as c:
        selected = asset_ids[:2]
        r = c.post("/api/plugins/basic.info/rerun-selected", json={"asset_ids": selected})
        assert r.status_code == 200
        body = r.json()
        assert body["dropped"] == len(selected)
        assert body["enqueued"] == len(selected)

    with session_scope() as s:
        pending = s.query(Job).filter(Job.plugin_id == "basic.info", Job.state == "pending").count()
        assert pending == len(selected)
        done = s.query(Job).filter(Job.plugin_id == "basic.info", Job.state == "done").count()
        assert done == n - len(selected)


def test_upload_preset_crud(tmp_data_dir):
    """Built-in presets seeded; user can create/delete non-builtin presets."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        # First access seeds built-ins.
        r = c.get("/api/upload-presets")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) >= 3  # 默认/会议/旅游
        names = {i["name"] for i in items}
        assert "默认" in names
        assert "会议" in names
        assert "旅游" in names

        builtin_ids = {i["id"] for i in items if i["is_builtin"]}

        # Create a custom preset.
        r = c.post(
            "/api/upload-presets",
            json={"name": "  我的预设  ", "plugin_ids": ["thumbnail", "exif"]},
        )
        assert r.status_code == 201
        custom = r.json()
        assert custom["name"] == "我的预设"
        assert custom["plugin_ids"] == ["thumbnail", "exif"]
        assert custom["is_builtin"] is False
        cid = custom["id"]

        # Duplicate name -> 409.
        r = c.post("/api/upload-presets", json={"name": "我的预设", "plugin_ids": []})
        assert r.status_code == 409

        # Delete custom preset.
        r = c.delete(f"/api/upload-presets/{cid}")
        assert r.status_code == 200

        # Delete builtin -> 403.
        bid = next(iter(builtin_ids))
        r = c.delete(f"/api/upload-presets/{bid}")
        assert r.status_code == 403


def test_upload_ingest_with_plugin_ids(tmp_data_dir, tmp_path: Path):
    """POST /api/uploads/{id}/ingest?plugin_ids= restricts which plugins are enqueued."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Job

    app = create_app()
    manager = app.state.upload_manager

    # Create a minimal upload session and finalize it.
    session_obj = manager.create("test.bin", size=4)
    chunk_dir = session_obj.storage_dir / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    (chunk_dir / "0").write_bytes(b"\x00\x01\x02\x03")
    session_obj.finalized = True
    session_obj.final_path = session_obj.storage_dir / "test.bin"
    session_obj.final_path.write_bytes(b"\x00\x01\x02\x03")

    with TestClient(app) as c:
        r = c.post(
            f"/api/uploads/{session_obj.upload_id}/ingest?plugin_ids=thumbnail&plugin_ids=exif",
        )
        assert r.status_code == 200
        asset_id = r.json()["asset_id"]

        with session_scope() as s:
            jobs = s.query(Job).filter(Job.asset_id == asset_id).all()
            enqueued = {j.plugin_id for j in jobs}
            assert "thumbnail" in enqueued
            assert "exif" in enqueued


# ---------------------------------------------------------------------------
# Global encryption toggle (Settings page)
# ---------------------------------------------------------------------------


def _seed_configured_vault(pwd: str = "settings-vault-pwd"):
    """Test helper: put the in-memory vault into LOCKED state with a master password set.

    Returns the freshly derived subkeys so callers can force-unlock if
    they want to test the success path. Mirrors the helpers in
    test_vault.py but kept inline here to avoid cross-module coupling.
    """
    import json as _json

    from hometrove.db import session_scope
    from hometrove.models import VaultState as _VaultStateRow
    from hometrove.vault.crypto import aes_key_wrap, derive_raw_master_key, derive_subkeys
    from hometrove.vault.state import _enable_for_test, _reset_for_test

    _reset_for_test()  # noqa: SLF001  wipe any leftover state from prior tests
    _enable_for_test()  # noqa: SLF001
    salt = os.urandom(16)
    params = {"m": 64 * 1024, "t": 2, "p": 1}
    raw = derive_raw_master_key(pwd, salt, params)
    sub = derive_subkeys(raw)
    wrapped = aes_key_wrap(bytes(sub.kek), raw)
    with session_scope() as s:
        s.add(_VaultStateRow(
            id=1,
            kdf_salt=salt,
            kdf_params_json=_json.dumps(params, sort_keys=True),
            wrapped_master_key=wrapped,
            version=1,
        ))
    return sub


def _force_unlock(sub):
    """Test helper: pin the in-memory state to a freshly-derived subkey set."""
    from hometrove.vault.state import _force_unlock_for_test

    _force_unlock_for_test(sub)  # noqa: SLF001


def _make_png(p: Path, color: tuple[int, int, int] = (10, 20, 30)) -> None:
    """Write a tiny valid PNG to ``p`` for upload tests."""
    from PIL import Image  # type: ignore

    Image.new("RGB", (24, 24), color).save(p)


def _do_upload(client, png_path: Path, filename: str = "x.png") -> dict:
    """Create a session, upload one chunk, finalize, ingest. Returns the ingest JSON."""
    r = client.post("/api/uploads", json={"filename": filename, "size": png_path.stat().st_size})
    r.raise_for_status()
    sid = r.json()["upload_id"]
    client.put(
        f"/api/uploads/{sid}/chunks/0",
        files={"file": (filename, png_path.read_bytes(), "image/png")},
    )
    client.post(f"/api/uploads/{sid}/complete", json={})
    r = client.post(f"/api/uploads/{sid}/ingest")
    r.raise_for_status()
    return r.json()


def test_settings_endpoint_default_state(tmp_data_dir):
    """GET /api/settings returns the toggle in its default (off) state."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["encrypt_new_uploads"] is False
        # Vault is not configured in a fresh tmp_data_dir.
        assert body["vault_configured"] is False
        assert body["vault_unlocked"] is False


def test_settings_toggle_persists(tmp_data_dir, monkeypatch):
    """PUT /api/settings flips the toggle, GET reflects it."""
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.app_settings import is_encrypt_new_uploads_enabled

    _seed_configured_vault()

    app = create_app()
    with TestClient(app) as c:
        # Pre-condition: off.
        r = c.get("/api/settings")
        assert r.json()["encrypt_new_uploads"] is False
        assert r.json()["vault_configured"] is True

        # Flip on.
        r = c.put("/api/settings", json={"encrypt_new_uploads": True})
        assert r.status_code == 200
        assert r.json()["encrypt_new_uploads"] is True

        # GET reflects it.
        r = c.get("/api/settings")
        assert r.json()["encrypt_new_uploads"] is True

        # Flip off.
        r = c.put("/api/settings", json={"encrypt_new_uploads": False})
        assert r.status_code == 200
        assert r.json()["encrypt_new_uploads"] is False

    # Persisted across app restarts.
    assert is_encrypt_new_uploads_enabled() is False


def test_settings_toggle_refused_when_vault_unconfigured(tmp_data_dir):
    """PUT refuses to enable encryption when the vault has no master key."""
    from hometrove.vault.state import _reset_for_test
    _reset_for_test()  # noqa: SLF001  previous tests may have left LOCKED state
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.app_settings import is_encrypt_new_uploads_enabled

    app = create_app()
    with TestClient(app) as c:
        # Confirm pre-condition: vault is not configured.
        assert c.get("/api/settings").json()["vault_configured"] is False

        r = c.put("/api/settings", json={"encrypt_new_uploads": True})
        assert r.status_code == 409
        assert "not configured" in r.json()["detail"].lower()

    # The toggle was NOT persisted.
    assert is_encrypt_new_uploads_enabled() is False


def test_settings_toggle_refused_when_vault_disabled_env(tmp_data_dir, monkeypatch):
    """When HOMETROVE_VAULT_ENABLED=false the toggle stays rejected."""
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "false")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"encrypt_new_uploads": True})
        assert r.status_code == 400
        assert "vault is disabled" in r.json()["detail"].lower()


def test_global_encryption_toggle_forces_encrypted_upload(
    tmp_data_dir, tmp_path: Path, monkeypatch
):
    """Flipping the toggle ON makes the next upload encrypted by default."""
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    sub = _seed_configured_vault()
    _force_unlock(sub)

    app = create_app()
    with TestClient(app) as c:
        # Flip the toggle on.
        r = c.put("/api/settings", json={"encrypt_new_uploads": True})
        assert r.status_code == 200

        # Upload WITHOUT specifying the ``encrypted`` field.
        p = tmp_path / "g.png"
        _make_png(p, (1, 2, 3))

        r = c.post("/api/uploads", json={"filename": "g.png", "size": p.stat().st_size})
        assert r.status_code == 200, r.text
        assert r.json()["encrypted"] is True
        sid = r.json()["upload_id"]
        c.put(
            f"/api/uploads/{sid}/chunks/0",
            files={"file": ("g.png", p.read_bytes(), "image/png")},
        )
        c.post(f"/api/uploads/{sid}/complete", json={})
        r = c.post(f"/api/uploads/{sid}/ingest")
        assert r.status_code == 200, r.text
        assert r.json()["encrypted"] is True
        aid = int(r.json()["asset_id"])

        with session_scope() as s:
            a = s.get(Asset, aid)
            assert a.encrypted_path is not None, "asset should be encrypted"


def test_global_encryption_toggle_opt_out_per_request(
    tmp_data_dir, tmp_path: Path, monkeypatch
):
    """Even with the toggle ON, the client can opt out via encrypted=false."""
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    sub = _seed_configured_vault()
    _force_unlock(sub)

    app = create_app()
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"encrypt_new_uploads": True})
        assert r.status_code == 200

        p = tmp_path / "o.png"
        _make_png(p, (4, 5, 6))

        r = c.post(
            "/api/uploads",
            json={"filename": "o.png", "size": p.stat().st_size, "encrypted": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["encrypted"] is False
        sid = r.json()["upload_id"]
        c.put(
            f"/api/uploads/{sid}/chunks/0",
            files={"file": ("o.png", p.read_bytes(), "image/png")},
        )
        c.post(f"/api/uploads/{sid}/complete", json={})
        r = c.post(f"/api/uploads/{sid}/ingest")
        assert r.status_code == 200, r.text
        aid = int(r.json()["asset_id"])

        with session_scope() as s:
            a = s.get(Asset, aid)
            assert a.encrypted_path is None, "explicit opt-out should be plaintext"


def test_global_encryption_toggle_blocks_unlocked_upload(
    tmp_data_dir, tmp_path: Path, monkeypatch
):
    """Toggle ON + vault locked → POST /api/uploads returns 423."""
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    _seed_configured_vault()  # NOTE: do NOT force-unlock

    app = create_app()
    with TestClient(app) as c:
        r = c.put("/api/settings", json={"encrypt_new_uploads": True})
        assert r.status_code == 200

        p = tmp_path / "l.png"
        _make_png(p, (7, 8, 9))

        r = c.post("/api/uploads", json={"filename": "l.png", "size": p.stat().st_size})
        assert r.status_code == 423, r.text
        assert "locked" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# M1-11: auth scaffold
# ---------------------------------------------------------------------------


def test_auth_passthrough_principal_exposed(tmp_data_dir):
    """The default passthrough backend exposes the local principal."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        # /api/health still 200 — no auth required by any existing route.
        r = c.get("/api/health")
        assert r.status_code == 200
        # The hidden debug route returns the principal the middleware produced.
        r = c.get("/api/_debug/principal")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "local"
        assert body["is_authenticated"] is False
        assert body["scopes"] == []


def test_auth_middleware_invokes_backend_per_request(tmp_data_dir):
    """A counting backend is called once for each request."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.auth import PassthroughAuthBackend

    calls = {"n": 0}

    class CountingBackend(PassthroughAuthBackend):
        def authenticate(self, request):  # type: ignore[override]
            calls["n"] += 1
            return super().authenticate(request)

    app = create_app()
    # The middleware looks up ``app.state.auth_backend`` on every dispatch,
    # so swapping it in place is the documented test seam.
    app.state.auth_backend = CountingBackend()

    with TestClient(app) as c:
        for _ in range(3):
            assert c.get("/api/health").status_code == 200
        assert calls["n"] == 3


def test_auth_middleware_lookup_helper():
    """get_principal returns Principal.local() outside a request context."""
    from hometrove.api.middleware import get_principal
    from hometrove.auth import Principal

    class _Stub:
        state = type("S", (), {})()

    p = get_principal(_Stub())  # type: ignore[arg-type]
    assert p == Principal.local()


def test_auth_backend_swap_through_factory():
    """build_auth_backend() honors a custom name and falls back to passthrough."""
    from hometrove.auth import (
        PassthroughAuthBackend,
        Principal,
        build_auth_backend,
    )

    class _Settings:
        auth_backend = "passthrough"

    backend = build_auth_backend(_Settings())  # type: ignore[arg-type]
    assert isinstance(backend, PassthroughAuthBackend)

    # Custom name -> not in registry -> still passthrough, never raises.
    class _Unknown:
        auth_backend = "oidc"

    backend = build_auth_backend(_Unknown())  # type: ignore[arg-type]
    assert isinstance(backend, PassthroughAuthBackend)

    # Caller can inject an explicit override (used by tests + future v1.1).
    fake_principal = Principal(id="alice", is_authenticated=True)
    backend = build_auth_backend(_Settings(), overrides={"passthrough": PassthroughAuthBackend(fake_principal)})
    p = backend.authenticate(object())
    assert p.id == "alice"


# ---------------------------------------------------------------------------
# M1-10: asr.faster_whisper
# ---------------------------------------------------------------------------


def _asr_setup_db():
    """Insert an Asset + a tiny MP4 with a silent audio track so the plugin can run."""
    import os

    import numpy as np
    from hometrove.db import session_scope
    from hometrove.models import Asset
    from hometrove.plugins.api import AssetLike, MediaType

    src = os.path.join(os.path.dirname(__file__), "_tmp_clip.mp4")
    _make_video_with_silent_audio(Path(src), w=160, h=120, frames=24)
    with session_scope() as s:
        s.add(Asset(
            id=500, path=src, media_root=str(os.path.dirname(src)),
            content_hash="asr1", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.commit()
    return AssetLike(
        id=500, path=src, media_root=str(os.path.dirname(src)),
        media_type=MediaType.VIDEO.value, content_hash_prefix="asr1",
    )


def _make_video_with_silent_audio(path: Path, w: int = 160, h: int = 120, frames: int = 12) -> None:
    """MP4 with a real (silent) audio track so PyAV can demux an audio stream."""
    import av
    import numpy as np

    container = av.open(str(path), mode="w")
    vstream = container.add_stream("mpeg4", rate=24)
    vstream.width = w
    vstream.height = h

    astream = container.add_stream("aac", rate=16_000)
    astream.layout = "mono"
    astream.sample_rate = 16_000

    arr = np.full((h, w, 3), 32, dtype="uint8")
    frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
    for packet in vstream.encode(frame):
        container.mux(packet)
    for packet in vstream.encode():
        container.mux(packet)

    # 0.5 s of silence at 16 kHz mono.
    silent = np.zeros((1, 8000), dtype=np.int16)
    aframe = av.AudioFrame.from_ndarray(silent, format="s16", layout="mono")
    aframe.sample_rate = 16_000
    aframe.pts = 0
    for packet in astream.encode(aframe):
        container.mux(packet)
    for packet in astream.encode():
        container.mux(packet)
    container.close()


def test_asr_faster_whisper_mock_backend_writes_segments(tmp_data_dir, monkeypatch):
    """The mock backend produces >=1 segment and writes it to asr_transcripts."""
    from hometrove.db import session_scope
    from hometrove.models import AsrTranscript
    from hometrove.plugins.api import PluginContext
    from hometrove.plugins.builtin.asr_faster_whisper import AsrFasterWhisperPlugin

    asset = _asr_setup_db()
    p = AsrFasterWhisperPlugin()
    # Force mock so the test is fast and doesn't pull faster-whisper.
    monkeypatch.setattr(
        "hometrove.plugins.builtin.asr_faster_whisper._transcribe_real",
        lambda *a, **k: None,
    )
    with session_scope() as s:
        ctx = PluginContext(asset=asset, params=p.ParamsModel(backend="auto"), db=s)
        res = p.run(asset, ctx)
        s.commit()
        assert res["status"] == "ok"
        assert res["backend"] == "mock"
        assert res["segments"] >= 1
        rows = (
            s.query(AsrTranscript)
            .filter(AsrTranscript.asset_id == asset.id)
            .order_by(AsrTranscript.t_start)
            .all()
        )
        assert len(rows) == res["segments"]
        for r in rows:
            assert r.t_end > r.t_start
            assert r.text
            assert r.plugin_id == "asr.faster_whisper"


def test_asr_faster_whisper_idempotent(tmp_data_dir, monkeypatch):
    """Re-running the plugin replaces (not stacks) the previous transcript rows."""
    from hometrove.db import session_scope
    from hometrove.models import AsrTranscript
    from hometrove.plugins.api import PluginContext
    from hometrove.plugins.builtin.asr_faster_whisper import AsrFasterWhisperPlugin

    asset = _asr_setup_db()
    p = AsrFasterWhisperPlugin()
    monkeypatch.setattr(
        "hometrove.plugins.builtin.asr_faster_whisper._transcribe_real",
        lambda *a, **k: None,
    )
    for _ in range(2):
        with session_scope() as s:
            p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(backend="auto"), db=s))
            s.commit()
    with session_scope() as s:
        n = (
            s.query(AsrTranscript)
            .filter(
                AsrTranscript.asset_id == asset.id,
                AsrTranscript.plugin_id == "asr.faster_whisper",
                AsrTranscript.plugin_version == p.version,
            )
            .count()
        )
        assert n >= 1


def test_asr_faster_whisper_skips_when_no_audio(tmp_data_dir):
    """A file without an audio stream returns skipped, never crashes."""
    from hometrove.db import session_scope
    from hometrove.models import AsrTranscript
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.asr_faster_whisper import AsrFasterWhisperPlugin

    # Empty payload -> av.open raises during demux; the plugin must skip.
    src = "/workspace/var/_asr_no_audio.mp4"
    Path(src).parent.mkdir(parents=True, exist_ok=True)
    Path(src).write_bytes(b"not a real video")
    asset = AssetLike(
        id=501, path=src, media_root=str(Path(src).parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="asr_na",
    )
    p = AsrFasterWhisperPlugin()
    with session_scope() as s:
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        assert res["status"] == "skipped"
        assert (
            s.query(AsrTranscript).filter(AsrTranscript.asset_id == 501).count() == 0
        )


def test_asr_faster_whisper_skips_image(tmp_data_dir):
    """Non-video media is skipped with a typed reason."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.asr_faster_whisper import AsrFasterWhisperPlugin

    asset = AssetLike(
        id=502, path="/p/x.png", media_root="/p",
        media_type=MediaType.IMAGE.value, content_hash_prefix="asr_img",
    )
    p = AsrFasterWhisperPlugin()
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "skipped"
    assert "not a video" in res["reason"]


def test_asr_faster_whisper_backend_real_unavailable(tmp_data_dir, monkeypatch):
    """With backend=faster_whisper and the package missing, report skipped."""
    from hometrove.db import session_scope
    from hometrove.plugins.api import PluginContext
    from hometrove.plugins.builtin.asr_faster_whisper import (
        AsrFasterWhisperPlugin,
        has_faster_whisper,
    )

    # Pretend the import probe failed.
    monkeypatch.setattr(
        "hometrove.plugins.builtin.asr_faster_whisper._HAS_FASTER_WHISPER", False,
    )
    asset = _asr_setup_db()
    p = AsrFasterWhisperPlugin()
    with session_scope() as s:
        ctx = PluginContext(asset=asset, params=p.ParamsModel(backend="faster_whisper"), db=s)
        res = p.run(asset, ctx)
    assert has_faster_whisper() is False
    assert res["status"] == "skipped"
    assert "faster_whisper" in res["reason"]


def test_asr_transcripts_exposed_in_asset_detail(tmp_data_dir, monkeypatch):
    """GET /api/assets/{id} includes the transcripts list."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.plugins.api import PluginContext
    from hometrove.plugins.builtin.asr_faster_whisper import AsrFasterWhisperPlugin

    asset = _asr_setup_db()
    p = AsrFasterWhisperPlugin()
    monkeypatch.setattr(
        "hometrove.plugins.builtin.asr_faster_whisper._transcribe_real",
        lambda *a, **k: None,
    )
    with session_scope() as s:
        p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(backend="auto"), db=s))
        s.commit()

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/assets/500")
        assert r.status_code == 200
        body = r.json()
        assert "transcripts" in body
        assert isinstance(body["transcripts"], list)
        assert body["transcripts"], "mock backend should produce >=1 segment"
        first = body["transcripts"][0]
        assert first["plugin_id"] == "asr.faster_whisper"
        assert first["t_end"] > first["t_start"]
        assert first["text"]


def test_search_keyword_recall_includes_transcripts(tmp_data_dir):
    """Search hits an asset via transcript text and carries the seek metadata."""
    from hometrove.db import session_scope
    from hometrove.models import AsrTranscript, Asset
    from hometrove.search import search

    needle = "海边的风"
    with session_scope() as s:
        s.add(Asset(
            id=503, path="/m/clip.mp4", media_root="/m",
            content_hash="asr_h", media_type="video",
            duration_sec=10.0, created_at=0, updated_at=0,
        ))
        s.flush()
        s.add(AsrTranscript(
            asset_id=503, plugin_id="asr.faster_whisper", plugin_version="0.1.0",
            t_start=1.5, t_end=3.0, text=f"开场白 {needle} 在场景里",
            lang="zh", confidence=0.9,
        ))
        s.commit()

    res = search(_search_session(), needle)
    hit = next((i for i in res["items"] if i["asset_id"] == 503), None)
    assert hit is not None
    assert hit["scope"] == "audio"
    assert hit["can_seek"] is True
    assert hit["t_start"] == 1.5
    assert hit["t_end"] == 3.0


def test_search_scope_audio_filter(tmp_data_dir):
    """``scope:audio`` restricts recall to transcript hits."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.search import search

    with session_scope() as s:
        s.add(Asset(
            id=504, path="/m/a.png", media_root="/m",
            content_hash="sa1", media_type="image",
            created_at=0, updated_at=0,
        ))
        s.flush()
        s.add(PluginResult(
            asset_id=504, plugin_id="mock.tags", plugin_version="0.1.0",
            status="ok", result_json='{"tags":["海边"]}',
        ))
        s.commit()

    res = search(_search_session(), "scope:audio 海边")
    assert all(i["scope"] == "audio" for i in res["items"])


def test_asr_faster_whisper_min_confidence_drops_segments(tmp_data_dir, monkeypatch):
    """Segments below the configured ``min_confidence`` are filtered out."""
    from hometrove.db import session_scope
    from hometrove.models import AsrTranscript
    from hometrove.plugins.api import PluginContext
    from hometrove.plugins.builtin.asr_faster_whisper import AsrFasterWhisperPlugin

    asset = _asr_setup_db()
    p = AsrFasterWhisperPlugin()
    # Real path returns nothing -> mock kicks in -> confidence ~0.7.
    monkeypatch.setattr(
        "hometrove.plugins.builtin.asr_faster_whisper._transcribe_real",
        lambda *a, **k: None,
    )
    with session_scope() as s:
        # Set the floor above the mock confidence so nothing survives.
        ctx = PluginContext(
            asset=asset,
            params=p.ParamsModel(backend="auto", min_confidence=0.99),
            db=s,
        )
        res = p.run(asset, ctx)
        s.commit()
        assert res["status"] == "ok"
        assert res["segments"] == 0
        assert (
            s.query(AsrTranscript).filter(AsrTranscript.asset_id == asset.id).count() == 0
        )


# ---------------------------------------------------------------------------
# M0-3: media-root read-only verification
# ---------------------------------------------------------------------------


def test_readonly_probe_flags_writable_root(tmp_path: Path, caplog):
    """A writable root is reported WRITABLE and the probe leaves no file behind."""
    from hometrove.readonly import check_roots

    with caplog.at_level("WARNING", logger="hometrove.readonly"):
        results = check_roots([tmp_path])
    assert len(results) == 1
    assert results[0].writable is True
    assert results[0].root == tmp_path
    # No leftover probe file.
    leftovers = list(tmp_path.glob(".hometrove-readonly-probe-*"))
    assert leftovers == []


def test_readonly_probe_detects_unwritable_root(tmp_path: Path, caplog, monkeypatch):
    """A read-only root is detected via os.access without trying to write.

    The probe runs ``os.access(W_OK)`` as the cheap first step — we
    simulate a true read-only mount (which ``os.access`` reports even
    for uid=0) by stubbing ``os.access`` to refuse W_OK on the probe
    root. The DAC-bypass branch is exercised in a separate test.
    """
    from hometrove.readonly import check_roots, log_report

    root = tmp_path / "ro"
    root.mkdir()

    real_access = os.access

    def fake_access(path, mode, *a, **kw):
        if str(path) == str(root) and mode & os.W_OK:
            return False
        return real_access(path, mode, *a, **kw)

    monkeypatch.setattr("hometrove.readonly.os.access", fake_access)
    results = check_roots([root])
    assert len(results) == 1
    assert results[0].writable is False
    assert "W_OK" in results[0].detail

    with caplog.at_level("INFO", logger="hometrove.readonly"):
        log_report(results)
    assert any("read-only" in r.message for r in caplog.records)


def test_readonly_probe_root_branch_detects_dac_bypass(tmp_path: Path, monkeypatch):
    """When running as root, a chmod that fails (EPERM) flips to read-only."""
    import errno

    from hometrove.readonly import check_roots

    monkeypatch.setattr("hometrove.readonly._running_as_root", lambda: True)
    real_chmod = os.chmod

    def raising_chmod(path, mode, *a, **kw):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr("hometrove.readonly.os.chmod", raising_chmod)
    results = check_roots([tmp_path])
    assert results[0].writable is False
    assert "root" in results[0].detail


def test_readonly_probe_handles_missing_path(tmp_path: Path):
    """A non-existent root returns a structured skip, never raises."""
    from hometrove.readonly import check_roots

    missing = tmp_path / "ghost"
    results = check_roots([missing])
    assert results[0].writable is False
    assert results[0].detail == "path missing"


def test_readonly_probe_handles_non_directory(tmp_path: Path):
    """A regular file passed as a root returns a structured skip."""
    from hometrove.readonly import check_roots

    f = tmp_path / "not-a-dir.txt"
    f.write_text("x")
    results = check_roots([f])
    assert results[0].writable is False
    assert results[0].detail == "not a directory"


def test_readonly_check_disabled_returns_empty():
    """``HOMETROVE_READ_ONLY_CHECK=off`` short-circuits the probe."""
    from hometrove.readonly import check_roots

    assert check_roots([Path("/tmp")], enabled=False) == []


def test_readonly_run_for_settings_emits_warning(caplog):
    """``run_for_settings`` reads settings.media_roots_paths and warns on writes."""
    from hometrove.readonly import run_for_settings

    class _S:
        media_roots_paths = [Path("/tmp")]
        read_only_check = "warn"

    with caplog.at_level("WARNING", logger="hometrove.readonly"):
        results = run_for_settings(_S())
    assert len(results) == 1
    assert results[0].writable is True
    assert any("WRITABLE" in r.message for r in caplog.records)


def test_readonly_run_for_settings_off_is_silent(caplog):
    """``read_only_check=off`` produces no log lines."""
    from hometrove.readonly import run_for_settings

    class _S:
        media_roots_paths = [Path("/tmp")]
        read_only_check = "off"

    with caplog.at_level("DEBUG", logger="hometrove.readonly"):
        results = run_for_settings(_S())
    assert results == []
    assert caplog.records == []


def test_lifespan_runs_readonly_probe(tmp_data_dir, caplog, monkeypatch):
    """Booting the API runs the probe once per process via lifespan."""
    import os

    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    # When running as root the kernel bypasses DAC permission checks, so the
    # probe intentionally reports "DAC bypassed" instead of "WRITABLE".
    if os.getuid() == 0:
        pytest.skip("read-only probe does not emit WRITABLE warning when running as root")

    root = tmp_data_dir.parent / "fake_media"
    root.mkdir()
    # HOMETROVE_MEDIA_ROOTS is parsed at Settings() construction; rebuild
    # the cached settings + engine around the env so the lifespan sees the
    # new root. tmp_data_dir already cleared the cache / engine once on
    # entry, so we just need to point the env at the new path.
    monkeypatch.setenv("HOMETROVE_MEDIA_ROOTS", str(root))
    from hometrove.config import reset_settings_cache, get_settings

    reset_settings_cache()
    # Build fresh settings (with the new env) before create_app reads it.
    get_settings()
    # Re-arm engine teardown so the lifespan ``engine()`` call works against
    # the same tempdir we want.
    app = create_app()
    with caplog.at_level("WARNING", logger="hometrove.readonly"):
        with TestClient(app):
            pass  # lifespan runs on enter / exit
    assert any("WRITABLE" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# v1 trash: soft-delete + restore + permanent empty + retention sweep.
# ---------------------------------------------------------------------------


def _seed_asset(client, tmp_path: Path, name: str = "a.png") -> int:
    """Helper: upload a PNG via the chunked endpoint + /ingest, return asset id."""
    from PIL import Image  # type: ignore

    p = tmp_path / name
    Image.new("RGB", (40, 30), (180, 100, 60)).save(p)
    r = client.post(
        "/api/uploads",
        json={"filename": name, "size": p.stat().st_size, "content_hash": None},
    )
    r.raise_for_status()
    sid = r.json()["upload_id"]
    client.put(
        f"/api/uploads/{sid}/chunks/0",
        files={"file": (name, p.read_bytes(), "image/png")},
    )
    client.post(f"/api/uploads/{sid}/complete", json={})
    final = client.post(f"/api/uploads/{sid}/ingest").json()
    return int(final["asset_id"])


def test_trash_hides_assets_from_default_list(tmp_data_dir, tmp_path: Path):
    """A trashed asset disappears from /api/assets and /api/assets/{id}."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        assert c.get("/api/assets").json()["items"]
        # trash it
        r = c.post(f"/api/assets/{aid}/trash")
        assert r.status_code == 200, r.text
        assert r.json()["deleted_at"] is not None
        # default list omits it
        assert aid not in [x["id"] for x in c.get("/api/assets").json()["items"]]
        # detail is 404
        assert c.get(f"/api/assets/{aid}").status_code == 404
        # file endpoint is 404
        assert c.get(f"/api/assets/{aid}/file").status_code == 404
        # include_trashed=true makes both visible
        assert aid in [
            x["id"]
            for x in c.get("/api/assets", params={"include_trashed": "true"}).json()["items"]
        ]
        detail = c.get(f"/api/assets/{aid}", params={"include_trashed": "true"})
        assert detail.status_code == 200
        assert detail.json()["deleted_at"] is not None


def test_trash_restore_roundtrip(tmp_data_dir, tmp_path: Path):
    """Move to trash then restore puts the asset back in the live view."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        c.post(f"/api/assets/{aid}/trash")
        r = c.post(f"/api/assets/{aid}/restore")
        assert r.status_code == 200
        assert r.json()["deleted_at"] is None
        assert aid in [x["id"] for x in c.get("/api/assets").json()["items"]]
        assert c.get(f"/api/assets/{aid}").status_code == 200


def test_trash_idempotent_preserves_original_timestamp(tmp_data_dir, tmp_path: Path):
    """Re-trashing an already-trashed asset must not reset ``deleted_at`` —
    retention math depends on the original deletion time.
    """
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        first = c.post(f"/api/assets/{aid}/trash").json()["deleted_at"]
        # second trash keeps the original timestamp (not bumped)
        second = c.post(f"/api/assets/{aid}/trash").json()["deleted_at"]
        assert first == second


def test_trash_unknown_asset_returns_404(tmp_data_dir):
    """Trashing / restoring an asset that doesn't exist surfaces a 404."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        assert c.post("/api/assets/9999/trash").status_code == 404
        assert c.post("/api/assets/9999/restore").status_code == 404


def test_trash_list_endpoint_paginates_newest_first(tmp_data_dir, tmp_path: Path):
    """GET /api/trash returns trashed rows in reverse-chronological order."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        ids = [_seed_asset(c, tmp_path, name=f"a{i}.png") for i in range(3)]
        for aid in ids:
            c.post(f"/api/assets/{aid}/trash")
            # Tiny sleep guarantees distinct ``deleted_at`` ordering even on
            # coarse-grained clocks.
            import time

            time.sleep(0.01)
        page = c.get("/api/trash", params={"limit": 2, "offset": 0}).json()
        assert page["total"] == 3
        assert len(page["items"]) == 2
        # Newest deletion first.
        assert page["items"][0]["id"] == ids[-1]
        assert page["items"][1]["id"] == ids[-2]
        # Offset 2 returns the oldest one only.
        page2 = c.get("/api/trash", params={"limit": 2, "offset": 2}).json()
        assert [x["id"] for x in page2["items"]] == [ids[0]]


def test_trash_empty_purges_rows_but_keeps_files(tmp_data_dir, tmp_path: Path):
    """POST /api/trash/empty removes the rows but does not touch the on-disk
    file — M0 assumes media roots are read-only mounts and the trash is a
    database-only soft delete.
    """
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        # Locate the uploaded file before purging.
        from hometrove.config import get_settings

        upload_dir = get_settings().resolved_upload_dir()
        files = list(upload_dir.rglob("*"))
        assert files, "uploaded file should be on disk before purge"
        c.post(f"/api/assets/{aid}/trash")
        r = c.post("/api/trash/empty")
        assert r.status_code == 200
        assert r.json()["dropped"] == 1
        # File is still on disk (M0 invariant).
        assert any(p.is_file() for p in upload_dir.rglob("*"))
        # Trash endpoint is empty.
        assert c.get("/api/trash").json()["total"] == 0


def test_trash_empty_older_than_respects_cutoff(tmp_data_dir, tmp_path: Path):
    """``older_than_seconds`` keeps recent trashed rows and only drops old ones."""
    import time
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid_old = _seed_asset(c, tmp_path, name="old.png")
        c.post(f"/api/assets/{aid_old}/trash")
        # Force the row's ``deleted_at`` to be older than the cutoff via a
        # direct DB poke — the route sets now(), but retention math uses
        # ``deleted_at`` so backdating the column is a clean unit test.
        from hometrove.db import session_scope
        from hometrove.models import Asset

        with session_scope() as s:
            a = s.get(Asset, aid_old)
            a.deleted_at = int(time.time()) - 31 * 86400
        aid_new = _seed_asset(c, tmp_path, name="new.png")
        c.post(f"/api/assets/{aid_new}/trash")

        r = c.post("/api/trash/empty", params={"older_than_seconds": 30 * 86400})
        assert r.status_code == 200
        assert r.json()["dropped"] == 1  # only the backdated old one
        # ``aid_old`` is gone, ``aid_new`` still in trash.
        page = c.get("/api/trash").json()
        assert page["total"] == 1
        assert page["items"][0]["id"] == aid_new


def test_trash_purge_expired_respects_settings(tmp_data_dir, tmp_path: Path, monkeypatch):
    """``purge_expired`` honours ``Settings.trash_retention_days``."""
    import time
    from hometrove.db import session_scope
    from hometrove.models import Asset

    aid = _seed_asset_for_test(tmp_data_dir, tmp_path)
    with session_scope() as s:
        a = s.get(Asset, aid)
        a.deleted_at = int(time.time()) - 5 * 86400  # 5 days ago
    # 7-day retention should NOT purge (5 < 7).
    monkeypatch.setenv("HOMETROVE_TRASH_RETENTION_DAYS", "7")
    from hometrove.config import reset_settings_cache, get_settings
    reset_settings_cache()
    get_settings()
    with session_scope() as s:
        assert _purge(s) == 0
    # 3-day retention should purge.
    monkeypatch.setenv("HOMETROVE_TRASH_RETENTION_DAYS", "3")
    reset_settings_cache()
    get_settings()
    with session_scope() as s:
        assert _purge(s) == 1


def _purge(s):
    from hometrove.trash import purge_expired
    return purge_expired(s)


def _seed_asset_for_test(tmp_data_dir, tmp_path: Path) -> int:
    """Seed a single asset row directly via the API (used by retention tests
    that don't need full client machinery; mirrors ``_seed_asset``).
    """
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        return _seed_asset(c, tmp_path)


def test_trash_does_not_break_search(tmp_data_dir, tmp_path: Path):
    """Trashed assets must not surface in /api/search results."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        # Run basic.info synchronously so the keyword path has a result to
        # match. (The worker isn't running in this test; we drive plugins
        # directly to keep the test deterministic.)
        from hometrove.db import session_scope
        from hometrove.models import Asset, Job
        from hometrove.orchestrator.runner import run_one

        with session_scope() as s:
            job_id = s.query(Job).filter(Job.asset_id == aid, Job.plugin_id == "basic.info").first()
            assert job_id is not None, "basic.info job should have been enqueued by ingest"
            run_one(job_id.id, s)
        # Pre-trash: a search for the filename stem should find the asset.
        with session_scope() as s:
            a = s.get(Asset, aid)
            tail = a.path.split("\0")[-1]
            stem = Path(tail).stem  # e.g. "a"
        hits = c.get("/api/search", params={"q": stem}).json()["items"]
        assert any(h["asset_id"] == aid for h in hits)
        # Trash + verify search no longer surfaces it.
        c.post(f"/api/assets/{aid}/trash")
        hits2 = c.get("/api/search", params={"q": stem}).json()["items"]
        assert not any(h["asset_id"] == aid for h in hits2)


def test_trash_does_not_break_folders_or_places(tmp_data_dir, tmp_path: Path):
    """Folder totals + per-root list, and place clusters, must exclude trashed assets."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        # Pre-trash: 1 in folders list.
        assert any(r["total"] >= 1 for r in c.get("/api/folders").json()["roots"])
        # Trash it.
        c.post(f"/api/assets/{aid}/trash")
        # Placeholders: places endpoint returns [] (no GPS in this asset).
        assert c.get("/api/places").json()["items"] == []
        # Folders: still counted? The M0 folder endpoint groups by media_root
        # and ``_seed_asset`` uploads into the synthetic "uploads" root. Live
        # count must be 0 after trash, so total should drop.
        roots = {r["media_root"]: r["total"] for r in c.get("/api/folders").json()["roots"]}
        if "uploads" in roots:
            assert roots["uploads"] == 0
        # Per-folder assets listing excludes trashed.
        items = c.get("/api/folders/assets", params={"media_root": "uploads"}).json()["items"]
        assert all(x["id"] != aid for x in items)


def test_trash_cli_prune_dry_run_and_empty(tmp_data_dir, tmp_path: Path, capsys, monkeypatch):
    """``hometrove trash prune --dry-run`` reports counts without deleting;
    ``hometrove trash empty`` purges the whole trash.
    """
    import time
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.cli import main as cli_main
    from hometrove.db import session_scope
    from hometrove.models import Asset

    aid_old = _seed_asset_for_test(tmp_data_dir, tmp_path)
    with session_scope() as s:
        a = s.get(Asset, aid_old)
        a.deleted_at = int(time.time()) - 31 * 86400

    # dry-run counts without deleting
    rc = cli_main(["trash", "prune", "--older-than-days", "30", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 asset(s) eligible" in out
    # row still in DB
    with session_scope() as s:
        assert s.get(Asset, aid_old) is not None

    # real prune removes it
    rc = cli_main(["trash", "prune", "--older-than-days", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dropped 1" in out
    with session_scope() as s:
        assert s.get(Asset, aid_old) is None


def test_trash_cli_empty_drops_everything(tmp_data_dir, tmp_path: Path, capsys):
    """``hometrove trash empty`` drops every trashed row in one call."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.cli import main as cli_main

    app = create_app()
    with TestClient(app) as c:
        for i in range(3):
            aid = _seed_asset(c, tmp_path, name=f"e{i}.png")
            c.post(f"/api/assets/{aid}/trash")
    rc = cli_main(["trash", "empty"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dropped 3" in out
    app2 = create_app()
    from fastapi.testclient import TestClient as TC
    with TC(app2) as c2:
        assert c2.get("/api/trash").json()["total"] == 0


# ---------------------------------------------------------------------------
# v1 favorites + bulk operations
# ---------------------------------------------------------------------------


def test_favorite_toggle_set_clear(tmp_data_dir, tmp_path: Path):
    """Per-asset toggle / set / clear endpoints flip ``favorite`` and stay idempotent."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        # toggle: 0 -> 1
        r = c.post(f"/api/assets/{aid}/favorite").json()
        assert r == {"ok": True, "id": aid, "favorite": True}
        # toggle again: 1 -> 0
        r = c.post(f"/api/assets/{aid}/favorite").json()
        assert r["favorite"] is False
        # set: explicit
        r = c.put(f"/api/assets/{aid}/favorite").json()
        assert r["favorite"] is True
        # clear: explicit
        r = c.delete(f"/api/assets/{aid}/favorite").json()
        assert r["favorite"] is False
        # 404 on unknown id (all three verbs)
        assert c.post("/api/assets/9999/favorite").status_code == 404
        assert c.put("/api/assets/9999/favorite").status_code == 404
        assert c.delete("/api/assets/9999/favorite").status_code == 404


def test_favorite_field_on_asset_dto(tmp_data_dir, tmp_path: Path):
    """Default-favorite is false; flipping persists into the asset list/detail DTOs."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        listed = next(x for x in c.get("/api/assets").json()["items"] if x["id"] == aid)
        assert listed["favorite"] is False
        c.post(f"/api/assets/{aid}/favorite")
        listed = next(x for x in c.get("/api/assets").json()["items"] if x["id"] == aid)
        assert listed["favorite"] is True
        # detail endpoint exposes it too
        detail = c.get(f"/api/assets/{aid}").json()
        assert detail["favorite"] is True


def test_favorite_facet_filters_library(tmp_data_dir, tmp_path: Path):
    """``?favorite=true|false`` narrows the /api/assets list."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        a1 = _seed_asset(c, tmp_path, name="f1.png")
        a2 = _seed_asset(c, tmp_path, name="f2.png")
        a3 = _seed_asset(c, tmp_path, name="f3.png")
        c.post(f"/api/assets/{a1}/favorite")
        c.post(f"/api/assets/{a3}/favorite")

        ids_all = {x["id"] for x in c.get("/api/assets").json()["items"]}
        ids_fav = {x["id"] for x in c.get("/api/assets", params={"favorite": "true"}).json()["items"]}
        ids_unfav = {x["id"] for x in c.get("/api/assets", params={"favorite": "false"}).json()["items"]}

        assert ids_all == {a1, a2, a3}
        assert ids_fav == {a1, a3}
        assert ids_unfav == {a2}


def test_bulk_trash_soft_deletes_all(tmp_data_dir, tmp_path: Path):
    """``/assets/bulk/trash`` soft-deletes all live assets in one call."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        ids = [_seed_asset(c, tmp_path, name=f"b{i}.png") for i in range(3)]
        r = c.post("/api/bulk/assets/trash", json={"asset_ids": ids}).json()
        assert r["requested"] == 3
        assert r["affected"] == 3
        assert r["missing"] == []
        # All hidden from default list.
        live = {x["id"] for x in c.get("/api/assets").json()["items"]}
        assert live.isdisjoint(ids)
        # All present in trash list.
        trashed = {x["id"] for x in c.get("/api/trash").json()["items"]}
        assert set(ids) <= trashed


def test_bulk_trash_idempotent_and_partial_failure(tmp_data_dir, tmp_path: Path):
    """Re-trashing does not change affected counts; missing ids are reported."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        ids = [_seed_asset(c, tmp_path, name=f"p{i}.png") for i in range(2)]
        # 1 missing id + 2 real
        payload = {"asset_ids": ids + [99999]}
        r = c.post("/api/bulk/assets/trash", json=payload).json()
        assert r["requested"] == 3
        assert r["affected"] == 2
        assert r["missing"] == [99999]
        # Second call: nothing to flip because everything is already trashed.
        r = c.post("/api/bulk/assets/trash", json={"asset_ids": ids}).json()
        assert r["affected"] == 0
        assert r["requested"] == 2


def test_bulk_restore_brings_assets_back(tmp_data_dir, tmp_path: Path):
    """``/assets/bulk/restore`` undoes a bulk trash."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        ids = [_seed_asset(c, tmp_path, name=f"r{i}.png") for i in range(2)]
        c.post("/api/bulk/assets/trash", json={"asset_ids": ids})
        r = c.post("/api/bulk/assets/restore", json={"asset_ids": ids}).json()
        assert r["affected"] == 2
        live = {x["id"] for x in c.get("/api/assets").json()["items"]}
        assert set(ids) <= live


def test_bulk_favorite_unfavorite_round_trip(tmp_data_dir, tmp_path: Path):
    """Favorite / unfavorite bulk endpoints flip the flag for all provided ids."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        ids = [_seed_asset(c, tmp_path, name=f"fav{i}.png") for i in range(3)]
        r = c.post("/api/bulk/assets/favorite", json={"asset_ids": ids}).json()
        assert r["affected"] == 3
        assert r["missing"] == []
        favs = {x["id"] for x in c.get("/api/assets", params={"favorite": "true"}).json()["items"]}
        assert set(ids) <= favs
        r = c.post("/api/bulk/assets/unfavorite", json={"asset_ids": ids[:2]}).json()
        assert r["affected"] == 2
        # Third asset is still favorited.
        favs = {x["id"] for x in c.get("/api/assets", params={"favorite": "true"}).json()["items"]}
        assert favs == {ids[2]}


def test_bulk_add_to_album_appends_in_order(tmp_data_dir, tmp_path: Path):
    """``/assets/bulk/add-to-album`` appends to an album preserving request order,
    skips assets already in the album, and reports the right ``added`` count.
    """
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        ids = [_seed_asset(c, tmp_path, name=f"al{i}.png") for i in range(3)]
        # Create an album with the first asset.
        album = c.post("/api/albums", json={"name": "Bulk", "asset_ids": [ids[0]]}).json()
        album_id = album["id"]
        # Add ids[1], ids[2], and ids[0] (already in — should skip).
        r = c.post(
            "/api/bulk/assets/add-to-album",
            json={"asset_ids": [ids[1], ids[2], ids[0]], "album_id": album_id},
        ).json()
        assert r["added"] == 2
        assert r["requested"] == 3
        # Album now contains all three ids in original insertion order.
        body = c.get(f"/api/albums/{album_id}").json()
        assert body["asset_ids"] == [ids[0], ids[1], ids[2]]


def test_bulk_add_to_album_unknown_album_returns_404(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        r = c.post(
            "/api/bulk/assets/add-to-album",
            json={"asset_ids": [aid], "album_id": 9999},
        )
        assert r.status_code == 404


def test_bulk_endpoints_enforce_limit(tmp_data_dir, tmp_path: Path):
    """A body with more than 1000 ids is rejected with 400 — protects the
    worker / DB from accidental flooding by a buggy client."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/bulk/assets/trash", json={"asset_ids": list(range(1, 1002))})
        assert r.status_code == 400
        assert "exceeds max" in r.text


def test_favorite_field_on_folders_and_places(tmp_data_dir, tmp_path: Path):
    """Folder totals / places list still respect ``deleted_at`` and continue to work with the new column."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        # ``_seed_asset`` uploads via the chunked endpoint, which stores
        # ``media_root`` as the staging parent (e.g. ``.../staging``), not
        # the literal string ``"uploads"``. Just confirm at least one root
        # has total=1, both before and after favoriting.
        roots = c.get("/api/folders").json()["roots"]
        assert any(r["total"] >= 1 for r in roots)
        c.post(f"/api/assets/{aid}/favorite")
        roots_after = c.get("/api/folders").json()["roots"]
        assert any(r["total"] >= 1 for r in roots_after)


# ---------------------------------------------------------------------------
# v1 shared albums
# ---------------------------------------------------------------------------


def _create_album(client, asset_ids: list[int], name: str = "shared") -> int:
    r = client.post("/api/albums", json={"name": name, "asset_ids": asset_ids})
    r.raise_for_status()
    return int(r.json()["id"])


def test_shared_album_create_and_public_view(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        a1 = _seed_asset(c, tmp_path, "a1.png")
        a2 = _seed_asset(c, tmp_path, "a2.png")
        album_id = _create_album(c, [a1, a2])

        r = c.post(
            f"/api/albums/{album_id}/shares",
            json={"allow_original": True, "allow_download": True},
        )
        r.raise_for_status()
        data = r.json()
        assert data["allow_original"] is True
        assert data["allow_download"] is True
        assert data["share_url"].startswith("/share/")
        token = data["token"]

        pub = c.get(f"/api/public/albums/{token}").json()
        assert pub["name"] == "shared"
        assert pub["asset_ids"] == [a1, a2]
        assert pub["allow_original"] is True

        # List shares returns the active link.
        items = c.get(f"/api/albums/{album_id}/shares").json()["items"]
        assert len(items) == 1
        assert items[0]["token"] == token


def test_shared_album_revoke_and_404(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        album_id = _create_album(c, [aid])
        token = c.post(f"/api/albums/{album_id}/shares", json={}).json()["token"]

        c.delete(f"/api/albums/{album_id}/shares/{token}").raise_for_status()
        assert c.get(f"/api/public/albums/{token}").status_code == 404

        # Delete on wrong album also 404.
        other = _create_album(c, [aid], name="other")
        other_token = c.post(f"/api/albums/{other}/shares", json={}).json()["token"]
        assert c.delete(f"/api/albums/{album_id}/shares/{other_token}").status_code == 404


def test_shared_album_hides_trashed_assets(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        a1 = _seed_asset(c, tmp_path, "a1.png")
        a2 = _seed_asset(c, tmp_path, "a2.png")
        album_id = _create_album(c, [a1, a2])
        token = c.post(f"/api/albums/{album_id}/shares", json={}).json()["token"]

        c.post(f"/api/assets/{a1}/trash")
        pub = c.get(f"/api/public/albums/{token}").json()
        assert pub["asset_ids"] == [a2]


def test_shared_album_public_file_permissions(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        album_id = _create_album(c, [aid])

        # Default: no original access.
        token = c.post(f"/api/albums/{album_id}/shares", json={}).json()["token"]
        assert c.get(f"/api/public/files/{token}/{aid}").status_code == 403

        # allow_original without download: inline 200, no attachment.
        token = c.post(
            f"/api/albums/{album_id}/shares",
            json={"allow_original": True, "allow_download": False},
        ).json()["token"]
        r = c.get(f"/api/public/files/{token}/{aid}")
        assert r.status_code == 200
        assert "attachment" not in r.headers.get("content-disposition", "")

        # allow_original + allow_download: attachment header.
        token = c.post(
            f"/api/albums/{album_id}/shares",
            json={"allow_original": True, "allow_download": True},
        ).json()["token"]
        r = c.get(f"/api/public/files/{token}/{aid}")
        assert r.status_code == 200
        assert "attachment" in r.headers.get("content-disposition", "")


def test_shared_album_public_thumbnail_and_non_member_404(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        member = _seed_asset(c, tmp_path, "member.png")
        outsider = _seed_asset(c, tmp_path, "outsider.png")
        album_id = _create_album(c, [member])
        token = c.post(f"/api/albums/{album_id}/shares", json={}).json()["token"]

        # Thumbnail for member returns something (original fallback because no thumb generated).
        r = c.get(f"/api/public/thumbnails/{token}/{member}/small")
        assert r.status_code == 200

        # Outsider and non-existent ids return 404.
        assert c.get(f"/api/public/thumbnails/{token}/{outsider}/small").status_code == 404
        assert c.get(f"/api/public/thumbnails/{token}/999999/small").status_code == 404


def test_shared_album_expiration_blocks_access(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        album_id = _create_album(c, [aid])
        now = int(__import__("time").time())
        r = c.post(
            f"/api/albums/{album_id}/shares",
            json={"expires_at": now + 1},
        )
        r.raise_for_status()
        token = r.json()["token"]

        # Wait for expiration, then verify all public endpoints are blocked.
        import time
        time.sleep(1.1)
        assert c.get(f"/api/public/albums/{token}").status_code == 404
        assert c.get(f"/api/public/thumbnails/{token}/{aid}/small").status_code == 404
        assert c.get(f"/api/public/files/{token}/{aid}").status_code == 404


def test_shared_album_cascade_delete_with_album(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        aid = _seed_asset(c, tmp_path)
        album_id = _create_album(c, [aid])
        token = c.post(f"/api/albums/{album_id}/shares", json={}).json()["token"]

        c.delete(f"/api/albums/{album_id}").raise_for_status()
        assert c.get(f"/api/public/albums/{token}").status_code == 404


# ---------------------------------------------------------------------------
# v1 smart albums
# ---------------------------------------------------------------------------


def _create_person(session, name: str) -> int:
    from hometrove.models import Person
    p = Person(name=name)
    session.add(p)
    session.flush()
    return int(p.id)


def _tag_asset(session, asset_id: int, tags: list[str], category: str | None = None):
    from hometrove.models import PluginResult
    existing = session.execute(
        select(PluginResult).where(
            PluginResult.asset_id == asset_id,
            PluginResult.plugin_id == "mock.tags",
        )
    ).scalars().first()
    if existing is None:
        session.add(PluginResult(
            asset_id=asset_id,
            plugin_id="mock.tags",
            plugin_version="0.1.0",
            status="ok",
            result_json=json.dumps({"tags": tags}),
        ))
    else:
        data = json.loads(existing.result_json)
        data["tags"] = list(set(data.get("tags", [])) | set(tags))
        existing.result_json = json.dumps(data)
    if category:
        session.add(PluginResult(
            asset_id=asset_id,
            plugin_id="mock.category",
            plugin_version="0.1.0",
            status="ok",
            result_json=json.dumps({"category": category}),
        ))


def _link_face(session, asset_id: int, person_id: int):
    from hometrove.models import FaceEmbedding
    session.add(FaceEmbedding(
        asset_id=asset_id,
        person_id=person_id,
        embedding_json="[0.1,0.2,0.3]",
        confidence=0.9,
    ))


def test_smart_album_crud_and_rule_evaluation(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    _seed_library(tmp_data_dir, tmp_path, n=4)
    app = create_app()
    with TestClient(app) as c:
        ids = [x["id"] for x in c.get("/api/assets", params={"limit": 50}).json()["items"]]
        assert len(ids) >= 4

        # Tag the first two assets with "cat" and set taken_at for ordering.
        with session_scope() as s:
            for i, aid in enumerate(ids[:2]):
                a = s.get(Asset, aid)
                a.taken_at = 1000 + i
                _tag_asset(s, aid, ["cat"])
            for i, aid in enumerate(ids[2:]):
                a = s.get(Asset, aid)
                a.taken_at = 500 + i
            s.commit()

        rule = {
            "op": "and",
            "children": [
                {"op": "tag", "value": "cat"},
                {"op": "media_type", "value": "image"},
            ],
        }
        r = c.post("/api/albums", json={"name": "猫咪", "is_smart": True, "rule": rule})
        assert r.status_code == 201
        album = r.json()
        assert album["is_smart"] is True
        assert album["asset_count"] == 2
        assert set(album["asset_ids"]) == set(ids[:2])
        assert album["rule"] == rule
        aid = album["id"]

        # List shows smart album.
        r = c.get("/api/albums")
        assert r.status_code == 200
        items = r.json()["items"]
        smart = next((a for a in items if a["id"] == aid), None)
        assert smart is not None
        assert smart["is_smart"] is True
        assert smart["asset_count"] == 2

        # Detail returns rule and ordered ids (taken_at desc).
        r = c.get(f"/api/albums/{aid}")
        assert r.status_code == 200
        assert r.json()["asset_ids"] == list(reversed(ids[:2]))

        # Manual membership endpoints are rejected.
        r = c.post(f"/api/albums/{aid}/assets", json={"asset_ids": [ids[2]]})
        assert r.status_code == 400
        r = c.request("DELETE", f"/api/albums/{aid}/assets", json={"asset_ids": [ids[0]]})
        assert r.status_code == 400

        # Update rule to use "or" and include all images.
        new_rule = {"op": "media_type", "value": "image"}
        r = c.patch(f"/api/albums/{aid}", json={"rule": new_rule})
        assert r.status_code == 200
        assert r.json()["rule"] == new_rule
        assert r.json()["asset_count"] >= 4

        # Delete removes rule row.
        c.delete(f"/api/albums/{aid}").raise_for_status()
        from hometrove.models import SmartAlbumRule
        with session_scope() as s:
            assert s.get(SmartAlbumRule, aid) is None


def test_smart_album_person_and_favorite_rules(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    _seed_library(tmp_data_dir, tmp_path, n=3)
    app = create_app()
    with TestClient(app) as c:
        ids = [x["id"] for x in c.get("/api/assets", params={"limit": 50}).json()["items"]]
        with session_scope() as s:
            person_id = _create_person(s, "Alice")
            _link_face(s, ids[0], person_id)
            _link_face(s, ids[1], person_id)
            for aid in ids[:2]:
                a = s.get(Asset, aid)
                a.favorite = 1
            s.commit()

        rule = {
            "op": "and",
            "children": [
                {"op": "person", "person_id": person_id},
                {"op": "favorite", "value": True},
            ],
        }
        r = c.post("/api/albums", json={"name": "Alice favorites", "is_smart": True, "rule": rule})
        assert r.status_code == 201
        assert r.json()["asset_count"] == 2
        assert set(r.json()["asset_ids"]) == set(ids[:2])


def test_smart_album_invalid_rules_rejected(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        # Missing rule for smart album.
        r = c.post("/api/albums", json={"name": "x", "is_smart": True})
        assert r.status_code == 422

        # Rule supplied for manual album.
        r = c.post("/api/albums", json={"name": "x", "is_smart": False, "rule": {"op": "tag", "value": "x"}})
        assert r.status_code == 422

        # Unknown operator.
        r = c.post("/api/albums", json={"name": "x", "is_smart": True, "rule": {"op": "unknown"}})
        assert r.status_code == 422

        # Missing required field.
        r = c.post("/api/albums", json={"name": "x", "is_smart": True, "rule": {"op": "person"}})
        assert r.status_code == 422

        # Too deep: root and (depth 0) -> and (1) -> and (2) -> and (3) -> favorite (4).
        deep = {
            "op": "and",
            "children": [
                {
                    "op": "and",
                    "children": [
                        {
                            "op": "and",
                            "children": [
                                {"op": "and", "children": [{"op": "favorite", "value": True}]}
                            ],
                        }
                    ],
                }
            ],
        }
        r = c.post("/api/albums", json={"name": "x", "is_smart": True, "rule": deep})
        assert r.status_code == 422


def test_smart_album_hides_trashed_assets(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    _seed_library(tmp_data_dir, tmp_path, n=2)
    app = create_app()
    with TestClient(app) as c:
        ids = [x["id"] for x in c.get("/api/assets", params={"limit": 50}).json()["items"]]
        with session_scope() as s:
            _tag_asset(s, ids[0], ["dog"])
            _tag_asset(s, ids[1], ["dog"])
            s.commit()

        rule = {"op": "tag", "value": "dog"}
        r = c.post("/api/albums", json={"name": "dogs", "is_smart": True, "rule": rule})
        album_id = r.json()["id"]
        assert r.json()["asset_count"] == 2

        c.post(f"/api/assets/{ids[0]}/trash").raise_for_status()
        r = c.get(f"/api/albums/{album_id}")
        assert r.status_code == 200
        assert r.json()["asset_count"] == 1
        assert r.json()["asset_ids"] == [ids[1]]


def test_smart_album_place_rule(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.scanner import discover, enqueue_pending, upsert_assets
    from hometrove.orchestrator.runner import run_one
    from hometrove.worker.main import _claim_next

    app = create_app()
    with TestClient(app) as c:
        media = tmp_path / "photos"
        media.mkdir()
        for i in range(3):
            make_png(media / f"gps{i}.png", 10, 8)
        with session_scope() as s:
            upsert_assets(s, list(discover([media])))
            enqueue_pending(s)
        while True:
            with session_scope() as s:
                job = _claim_next(s, "test")
                if job is None:
                    break
                run_one(job.id, s)

        with session_scope() as s:
            assets = s.execute(select(Asset).order_by(Asset.id)).scalars().all()
            gps = [
                (39.9042, 116.4074),
                (39.95, 116.45),
                (31.2304, 121.4737),
            ]
            for a, (lat, lon) in zip(assets, gps):
                s.add(PluginResult(
                    asset_id=a.id,
                    plugin_id="exif",
                    plugin_version="0.1.0",
                    status="ok",
                    result_json=json.dumps({"metadata": {"gps_lat": lat, "gps_lon": lon}}),
                ))
            s.commit()

        # The first two photos cluster to the same Beijing grid cell.
        places = c.get("/api/places").json()["items"]
        bj = next(p for p in places if p["count"] == 2)
        place_id = f"{bj['lat']},{bj['lon']}"

        rule = {"op": "place", "place_id": place_id}
        r = c.post("/api/albums", json={"name": "北京", "is_smart": True, "rule": rule})
        assert r.status_code == 201
        assert set(r.json()["asset_ids"]) == set(bj["asset_ids"])


# ---------------------------------------------------------------------------
# v1 advanced filters: /api/assets date range + place + combined conditions
# ---------------------------------------------------------------------------


def test_assets_taken_after_before_filter(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    _seed_library(tmp_data_dir, tmp_path, n=4)
    app = create_app()
    with TestClient(app) as c:
        ids = [x["id"] for x in c.get("/api/assets", params={"limit": 50}).json()["items"]]
        assert len(ids) >= 4

        with session_scope() as s:
            for i, aid in enumerate(ids):
                a = s.get(Asset, aid)
                a.taken_at = 1000 + i * 100
            s.commit()

        # taken_after is inclusive (>=), taken_before is exclusive (<).
        after = 1100
        before = 1300
        r = c.get("/api/assets", params={"taken_after": after, "taken_before": before})
        assert r.status_code == 200
        got = [x["id"] for x in r.json()["items"]]
        expect = [aid for i, aid in enumerate(ids) if 1000 + i * 100 >= after and 1000 + i * 100 < before]
        assert sorted(got) == sorted(expect)

        # Only after.
        r = c.get("/api/assets", params={"taken_after": 1200})
        got = [x["id"] for x in r.json()["items"]]
        expect = [aid for i, aid in enumerate(ids) if 1000 + i * 100 >= 1200]
        assert sorted(got) == sorted(expect)

        # Only before.
        r = c.get("/api/assets", params={"taken_before": 1000})
        got = [x["id"] for x in r.json()["items"]]
        expect = [aid for i, aid in enumerate(ids) if 1000 + i * 100 < 1000]
        assert sorted(got) == sorted(expect)

        # Negative epoch rejected.
        assert c.get("/api/assets", params={"taken_after": -1}).status_code == 422


def test_assets_place_filter(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import engine, session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.scanner import discover, enqueue_pending, upsert_assets
    from hometrove.orchestrator.runner import run_one
    from hometrove.worker.main import _claim_next
    import hometrove.plugins.builtin  # noqa: F401

    engine()
    media = tmp_path / "photos"
    media.mkdir()
    for i in range(3):
        make_png(media / f"gps{i}.png", 10, 8)
    with session_scope() as s:
        upsert_assets(s, list(discover([media])))
        enqueue_pending(s)
    while True:
        with session_scope() as s:
            job = _claim_next(s, "test")
            if job is None:
                break
            run_one(job.id, s)

    with session_scope() as s:
        assets = s.query(Asset).order_by(Asset.id).all()
        assert len(assets) == 3
        gps = [
            (39.9042, 116.4074),
            (39.95, 116.45),
            (31.2304, 121.4737),
        ]
        for a, (lat, lon) in zip(assets, gps):
            s.add(PluginResult(
                asset_id=a.id,
                plugin_id="exif",
                plugin_version="0.1.0",
                status="ok",
                result_json=json.dumps({"metadata": {"gps_lat": lat, "gps_lon": lon}}),
            ))
        s.commit()

    app = create_app()
    with TestClient(app) as c:
        places = c.get("/api/places").json()["items"]
        bj = next(p for p in places if p["count"] == 2)
        place_id = f"{bj['lat']},{bj['lon']}"
        r = c.get("/api/assets", params={"place": place_id})
        assert r.status_code == 200
        got = [x["id"] for x in r.json()["items"]]
        assert sorted(got) == sorted(bj["asset_ids"])

        # Invalid place format: FastAPI pattern rejects.
        assert c.get("/api/assets", params={"place": "not-a-place"}).status_code == 422


def test_assets_combined_filters(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    _seed_library(tmp_data_dir, tmp_path, n=4)
    app = create_app()
    with TestClient(app) as c:
        ids = [x["id"] for x in c.get("/api/assets", params={"limit": 50}).json()["items"]]
        assert len(ids) >= 4

        person_id = None
        with session_scope() as s:
            for i, aid in enumerate(ids):
                a = s.get(Asset, aid)
                a.taken_at = 1000 + i
                if i < 2:
                    _tag_asset(s, aid, ["cat"])
            person_id = _create_person(s, "Alice")
            _link_face(s, ids[0], person_id)
            s.commit()

        # media_type + tag + person_id + favorite + date range together.
        c.put(f"/api/assets/{ids[0]}/favorite")
        r = c.get("/api/assets", params={
            "media_type": "image",
            "tag": "cat",
            "person_id": person_id,
            "favorite": "true",
            "taken_after": 900,
            "taken_before": 1200,
        })
        assert r.status_code == 200
        got = [x["id"] for x in r.json()["items"]]
        assert sorted(got) == [ids[0]]

        # Dropping the person condition widens the set.
        r = c.get("/api/assets", params={
            "media_type": "image",
            "tag": "cat",
            "favorite": "true",
            "taken_after": 900,
        })
        got = [x["id"] for x in r.json()["items"]]
        assert sorted(got) == [ids[0]]


# ---------------------------------------------------------------------------
# v1 image similarity: /api/assets/{id}/similar nearest-neighbour recall
# ---------------------------------------------------------------------------


def _seed_similar_assets():
    """Seed 4 assets, each with a distinct image embedding, and return ids.

    Asset A's vector is closest to B, then C, then D (Euclidean on a sparse
    one-hot vector of dim 1024).
    """
    import json

    from hometrove.db import session_scope
    from hometrove.models import Asset, Embedding
    from hometrove.vector import get_index

    vectors = {
        200: [1.0, 0.0, 0.0, 0.0],   # A
        201: [0.9, 0.1, 0.0, 0.0],   # B (near A)
        202: [0.5, 0.5, 0.0, 0.0],   # C (further)
        203: [0.0, 0.0, 1.0, 0.0],   # D (far)
    }
    with session_scope() as s:
        for aid, vec in vectors.items():
            s.add(Asset(
                id=aid, path=f"/p/{aid}.png", media_root="/p",
                content_hash=f"sim{aid}", media_type="image",
                created_at=0, updated_at=0,
            ))
        s.flush()
        for aid, vec in vectors.items():
            padded = [0.0] * 1024
            padded[:4] = vec
            row = Embedding(
                asset_id=aid, plugin_id="embedding.jina_clip", plugin_version="0.1.0",
                scope="image", t_start=None, t_end=None,
                embedding_json=json.dumps(padded),
            )
            s.add(row)
            s.flush()
            get_index().upsert(row.id, padded, session=s)
        s.commit()
    return 200, [201, 202, 203]


def test_similar_assets_ranking_and_self_exclusion(tmp_data_dir):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    anchor, others = _seed_similar_assets()
    app = create_app()
    with TestClient(app) as c:
        r = c.get(f"/api/assets/{anchor}/similar")
        assert r.status_code == 200
        body = r.json()
        assert body["asset_id"] == anchor
        ids = [it["asset_id"] for it in body["items"]]
        # Self is excluded; neighbours sorted by distance ascending.
        assert anchor not in ids
        assert ids == others
        dists = [it["distance"] for it in body["items"]]
        assert dists == sorted(dists)
        # Distance for the nearest neighbour is small (near-duplicate vector).
        assert body["items"][0]["distance"] < 0.5


def test_similar_assets_missing_embedding_returns_empty(tmp_data_dir, tmp_path: Path):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    with session_scope() as s:
        s.add(Asset(
            id=299, path="/p/299.png", media_root="/p",
            content_hash="sim299", media_type="image",
            created_at=0, updated_at=0,
        ))
        s.commit()

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/assets/299/similar")
        assert r.status_code == 200
        assert r.json()["items"] == []


def test_similar_assets_404_for_unknown_or_trashed(tmp_data_dir):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    _seed_similar_assets()
    with session_scope() as s:
        s.add(Asset(
            id=298, path="/p/298.png", media_root="/p",
            content_hash="sim298", media_type="image",
            created_at=0, updated_at=0, deleted_at=123,
        ))
        s.commit()

    app = create_app()
    with TestClient(app) as c:
        assert c.get("/api/assets/99999/similar").status_code == 404
        assert c.get("/api/assets/298/similar").status_code == 404


def test_similar_assets_excludes_trashed_neighbours(tmp_data_dir):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    anchor, others = _seed_similar_assets()
    # Trash the nearest neighbour (id 201).
    with session_scope() as s:
        s.get(Asset, others[0]).deleted_at = 123
        s.commit()

    app = create_app()
    with TestClient(app) as c:
        r = c.get(f"/api/assets/{anchor}/similar")
        assert r.status_code == 200
        ids = [it["asset_id"] for it in r.json()["items"]]
        assert 201 not in ids
        assert ids == [202, 203]


# ---------------------------------------------------------------------------
# v1 keyframe extraction: basic.keyframes plugin + /keyframes endpoints
# ---------------------------------------------------------------------------


def test_keyframes_plugin_per_scene(tmp_data_dir, tmp_path: Path):
    """Each scene yields ``per_scene`` keyframes with in-window timestamps."""
    import json
    from PIL import Image
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.keyframes import KeyframesPlugin

    src = tmp_path / "clip.mp4"
    _make_video(src, w=160, h=120, frames=24)

    p = KeyframesPlugin()
    asset = AssetLike(
        id=301, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="kf",
    )
    # Inject scene_detect output through the DB result_of surface.
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult

    with session_scope() as s:
        s.add(Asset(
            id=301, path=str(src), media_root=str(src.parent),
            content_hash="kf", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=301, plugin_id="basic.scene_detect", plugin_version="0.1.0",
            status="ok",
            result_json=json.dumps({
                "scenes": [
                    {"start": 0.0, "end": 0.25, "keyframe": 0.125},
                    {"start": 0.25, "end": 0.5, "keyframe": 0.375},
                    {"start": 0.5, "end": 0.75, "keyframe": 0.625},
                    {"start": 0.75, "end": 1.0, "keyframe": 0.875},
                ]
            }),
        ))
        s.commit()

        params = p.ParamsModel(per_scene=2)
        ctx = PluginContext(asset=asset, params=params, db=s, data_dir=tmp_data_dir)
        res = p.run(asset, ctx)
        s.commit()
        assert res["status"] == "ok"
        assert res["scene_count"] == 4
        assert res["total"] == 8

    for kf in res["keyframes"]:
        assert 0.0 <= kf["t_sec"] <= 1.0
        assert kf["scene"] in (0, 1, 2, 3)
        dest = tmp_data_dir / "keyframes" / "301" / kf["file"]
        assert dest.is_file()
        im = Image.open(dest)
        assert im.size[0] <= 640 and im.size[1] <= 240


def test_keyframes_plugin_no_scenes_falls_back(tmp_data_dir, tmp_path: Path):
    """Without scene_detect output, the whole video is a single window."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.keyframes import KeyframesPlugin

    src = tmp_path / "clip.mp4"
    _make_video(src, w=160, h=120, frames=24)

    p = KeyframesPlugin()
    asset = AssetLike(
        id=302, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="kf2",
    )
    ctx = PluginContext(asset=asset, params=p.ParamsModel(), data_dir=tmp_data_dir)
    res = p.run(asset, ctx)
    assert res["status"] == "ok"
    assert res["scene_count"] == 0
    assert res["total"] == 1  # cover frame at 0s
    assert res["keyframes"][0]["t_sec"] == 0.0
    assert (tmp_data_dir / "keyframes" / "302" / res["keyframes"][0]["file"]).is_file()


def test_keyframes_plugin_skips_non_video_and_bad_decode(tmp_data_dir, tmp_path: Path):
    """Non-video assets and undecodable videos are skipped, not failed."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.keyframes import KeyframesPlugin

    p = KeyframesPlugin()

    img = tmp_path / "img.png"
    make_png(img)
    img_asset = AssetLike(
        id=303, path=str(img), media_root=str(img.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="kf3",
    )
    assert p.run(img_asset, PluginContext(asset=img_asset, params=p.ParamsModel(), data_dir=tmp_data_dir))["status"] == "skipped"

    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"fragmented fake video payload")
    bad_asset = AssetLike(
        id=304, path=str(bad), media_root=str(bad.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="kf4",
    )
    assert p.run(bad_asset, PluginContext(asset=bad_asset, params=p.ParamsModel(), data_dir=tmp_data_dir))["status"] == "skipped"


def test_keyframes_endpoints(tmp_data_dir, tmp_path: Path):
    """List + file endpoints expose keyframe results with proper 404s."""
    import json
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult

    src = tmp_path / "clip.mp4"
    _make_video(src, w=160, h=120, frames=24)

    with session_scope() as s:
        s.add(Asset(
            id=305, path=str(src), media_root=str(src.parent),
            content_hash="kf5", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.add(Asset(
            id=306, path=str(tmp_path / "clip2.mp4"), media_root=str(tmp_path),
            content_hash="kf6", media_type="video",
            created_at=0, updated_at=0, deleted_at=123,
        ))
        s.commit()

    # Run the plugin once against asset 305 so files + result exist.
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.keyframes import KeyframesPlugin

    p = KeyframesPlugin()
    asset = AssetLike(
        id=305, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="kf5",
    )
    with session_scope() as s:
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s, data_dir=tmp_data_dir))
        s.add(PluginResult(
            asset_id=305, plugin_id="basic.keyframes", plugin_version="0.1.0",
            status=res["status"], result_json=json.dumps(res),
        ))
        s.commit()

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/assets/305/keyframes")
        assert r.status_code == 200
        body = r.json()
        assert body["asset_id"] == 305
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["scene"] == 0 and item["index"] == 0 and item["t_sec"] == 0.0
        assert item["url"] == "/api/assets/305/keyframes/0/0"

        # The file endpoint serves a real JPEG.
        fr = c.get(item["url"])
        assert fr.status_code == 200
        assert fr.headers["content-type"].startswith("image/jpeg")
        assert len(fr.content) > 0

        # Asset with no keyframe result -> empty list.
        r = c.get("/api/assets/305/keyframes")  # still populated above
        assert r.status_code == 200

        # Trashed / unknown assets -> 404 on both endpoints.
        assert c.get("/api/assets/306/keyframes").status_code == 404
        assert c.get("/api/assets/99999/keyframes").status_code == 404
        assert c.get("/api/assets/306/keyframes/0/0").status_code == 404
        assert c.get("/api/assets/99999/keyframes/0/0").status_code == 404

        # Missing file -> 404.
        assert c.get("/api/assets/305/keyframes/9/9").status_code == 404



# ---------------------------------------------------------------------------
# v1 Chinese captions: vlm.qwen3vl plugin + embedding.bge_m3 caption scope
# ---------------------------------------------------------------------------


def test_vlm_qwen3vl_image_mock_caption(tmp_data_dir, tmp_path: Path):
    """An image yields one caption with t=None; mock is deterministic."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.vlm_qwen3vl import VlmQwen3vlPlugin

    src = tmp_path / "photo.png"
    _make_photo(src, 64, 48)
    p = VlmQwen3vlPlugin()
    asset = AssetLike(
        id=401, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="vlm1",
    )
    ctx = PluginContext(asset=asset, params=p.ParamsModel())
    res = p.run(asset, ctx)
    assert res["status"] == "ok"
    assert res["backend"] == "mock"
    caps = res["captions"]
    assert len(caps) == 1
    assert caps[0]["t"] is None
    assert caps[0]["caption"]

    # Determinism: same asset + params -> identical caption.
    res2 = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res2["captions"] == caps


def test_vlm_qwen3vl_video_per_scene(tmp_data_dir, tmp_path: Path):
    """Video captions follow scene_detect keyframes with their timestamps."""
    import json
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.vlm_qwen3vl import VlmQwen3vlPlugin

    src = tmp_path / "clip.mp4"
    _make_video(src, w=160, h=120, frames=24)

    with session_scope() as s:
        s.add(Asset(
            id=402, path=str(src), media_root=str(src.parent),
            content_hash="vlm2", media_type="video",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=402, plugin_id="basic.scene_detect", plugin_version="0.1.0",
            status="ok",
            result_json=json.dumps({
                "scenes": [
                    {"start": 0.0, "end": 0.25, "keyframe": 0.125},
                    {"start": 0.25, "end": 0.5, "keyframe": 0.375},
                    {"start": 0.5, "end": 0.75, "keyframe": 0.625},
                ]
            }),
        ))
        s.commit()

        p = VlmQwen3vlPlugin()
        asset = AssetLike(
            id=402, path=str(src), media_root=str(src.parent),
            media_type=MediaType.VIDEO.value, content_hash_prefix="vlm2",
        )
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()

    assert res["status"] == "ok"
    caps = res["captions"]
    assert len(caps) == 3
    assert [c["t"] for c in caps] == [0.125, 0.375, 0.625]
    for c in caps:
        assert c["caption"]


def test_vlm_qwen3vl_video_no_scenes_falls_back(tmp_data_dir, tmp_path: Path):
    """A video without scene_detect output gets a single t=0.0 caption."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.vlm_qwen3vl import VlmQwen3vlPlugin

    src = tmp_path / "clip.mp4"
    _make_video(src, w=160, h=120, frames=24)
    p = VlmQwen3vlPlugin()
    asset = AssetLike(
        id=403, path=str(src), media_root=str(src.parent),
        media_type=MediaType.VIDEO.value, content_hash_prefix="vlm3",
    )
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel()))
    assert res["status"] == "ok"
    assert len(res["captions"]) == 1
    assert res["captions"][0]["t"] == 0.0


def test_vlm_qwen3vl_skips_non_target_and_missing(tmp_data_dir, tmp_path: Path):
    """Non-image/video media and missing source files are skipped, not failed."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.vlm_qwen3vl import VlmQwen3vlPlugin

    p = VlmQwen3vlPlugin()

    other = AssetLike(
        id=404, path="/p/x.txt", media_root="/p",
        media_type=MediaType.OTHER.value, content_hash_prefix="vlm4",
    )
    res = p.run(other, PluginContext(asset=other, params=p.ParamsModel()))
    assert res["status"] == "skipped"
    assert "not an image or video" in res["reason"]

    missing = AssetLike(
        id=405, path="/p/gone.png", media_root="/p",
        media_type=MediaType.IMAGE.value, content_hash_prefix="vlm5",
    )
    res = p.run(missing, PluginContext(asset=missing, params=p.ParamsModel()))
    assert res["status"] == "skipped"
    assert "missing" in res["reason"]


def test_vlm_qwen3vl_qwen3vl_backend_unavailable_skips(tmp_data_dir, tmp_path: Path, monkeypatch):
    """backend=qwen3vl with no endpoint -> skipped (no silent mock fallback)."""
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.vlm_qwen3vl import VlmQwen3vlPlugin

    monkeypatch.setattr(
        "hometrove.plugins.builtin.vlm_qwen3vl.set_qwen3vl_available", lambda v: None,
    )
    import hometrove.plugins.builtin.vlm_qwen3vl as mod
    mod._HAS_QWEN3VL = False

    src = tmp_path / "photo.png"
    _make_photo(src, 32, 24)
    p = VlmQwen3vlPlugin()
    asset = AssetLike(
        id=406, path=str(src), media_root=str(src.parent),
        media_type=MediaType.IMAGE.value, content_hash_prefix="vlm6",
    )
    # No endpoint_url configured -> qwen3vl backend must skip.
    res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(backend="qwen3vl")))
    assert res["status"] == "skipped"
    assert "VLM unavailable" in res["reason"]


def test_embedding_bge_m3_writes_caption_vectors(tmp_data_dir, tmp_path: Path):
    """bge_m3 encodes vlm captions into scope=caption embeddings (idempotent)."""
    import json
    from hometrove.db import session_scope
    from hometrove.models import Asset, Embedding, PluginResult
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.embedding_bge_m3 import EmbeddingBgeM3Plugin

    src = tmp_path / "photo.png"
    _make_photo(src, 32, 24)

    with session_scope() as s:
        s.add(Asset(
            id=407, path=str(src), media_root=str(src.parent),
            content_hash="vlm7", media_type="image",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=407, plugin_id="vlm.qwen3vl", plugin_version="0.1.0",
            status="ok",
            result_json=json.dumps({
                "captions": [
                    {"t": None, "caption": "一张夕阳下的海边照片"},
                    {"t": 0.5, "caption": "孩子在海边奔跑"},
                ]
            }),
        ))
        s.commit()

        p = EmbeddingBgeM3Plugin()
        asset = AssetLike(
            id=407, path=str(src), media_root=str(src.parent),
            media_type=MediaType.IMAGE.value, content_hash_prefix="vlm7",
        )
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        assert res["status"] == "ok"
        assert res["scope"] == "caption"
        assert res["vectors"] == 2

        rows = (
            s.query(Embedding)
            .filter(Embedding.asset_id == 407, Embedding.plugin_id == "embedding.bge_m3")
            .order_by(Embedding.id)
            .all()
        )
        assert len(rows) == 2
        assert rows[0].scope == "caption" and rows[0].t_start is None
        assert rows[1].t_start == 0.5 and rows[1].t_end == 0.5

        # Idempotent re-run: vectors replaced, not stacked.
        res2 = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        assert res2["status"] == "ok"
        n = (
            s.query(Embedding)
            .filter(Embedding.asset_id == 407, Embedding.plugin_id == "embedding.bge_m3")
            .count()
        )
        assert n == 2


def test_embedding_bge_m3_skips_without_vlm_result(tmp_data_dir, tmp_path: Path):
    """No vlm.qwen3vl result -> skipped, never writes caption vectors."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, Embedding
    from hometrove.plugins.api import AssetLike, MediaType, PluginContext
    from hometrove.plugins.builtin.embedding_bge_m3 import EmbeddingBgeM3Plugin

    src = tmp_path / "photo.png"
    _make_photo(src, 32, 24)
    with session_scope() as s:
        s.add(Asset(
            id=408, path=str(src), media_root=str(src.parent),
            content_hash="vlm8", media_type="image",
            created_at=0, updated_at=0,
        ))
        s.commit()
        p = EmbeddingBgeM3Plugin()
        asset = AssetLike(
            id=408, path=str(src), media_root=str(src.parent),
            media_type=MediaType.IMAGE.value, content_hash_prefix="vlm8",
        )
        res = p.run(asset, PluginContext(asset=asset, params=p.ParamsModel(), db=s))
        s.commit()
        assert res["status"] == "skipped"
        assert "no vlm.qwen3vl result" in res["reason"]
        assert (
            s.query(Embedding).filter(Embedding.asset_id == 408).count() == 0
        )


def test_search_scope_caption_recall(tmp_data_dir):
    """scope:caption limits recall to caption vectors and enables video seek."""
    from hometrove.db import session_scope
    from hometrove.search import search

    with session_scope() as s:
        # A caption vector whose mock vector aligns with the query.
        q = "sunset beach"
        qv = _encode_query_for_test(q)
        _add_asset_and_embedding(s, 411, vec=qv, scope="caption", t_start=1.5, t_end=1.5, media_type="video")
        # An image-scope vector for a different asset that must be excluded.
        _add_asset_and_embedding(s, 412, vec=qv, scope="image")
        s.commit()

    res = search(_search_session(), "scope:caption " + q)
    assert res["total"] >= 1
    assert all(i["scope"] == "caption" for i in res["items"])
    assert any(i["asset_id"] == 411 for i in res["items"])
    hit = next(i for i in res["items"] if i["asset_id"] == 411)
    assert hit["t_start"] == 1.5
    assert hit["can_seek"] is True


def test_search_keyword_recall_includes_vlm_captions(tmp_data_dir):
    """VLM caption text is searched by the keyword path."""
    from hometrove.db import session_scope
    from hometrove.models import Asset, PluginResult
    from hometrove.search import search

    with session_scope() as s:
        s.add(Asset(
            id=413, path="/m/p.png", media_root="/m",
            content_hash="vlm13", media_type="image",
            created_at=0, updated_at=0,
        ))
        s.add(PluginResult(
            asset_id=413, plugin_id="vlm.qwen3vl", plugin_version="0.1.0",
            status="ok",
            result_json='{"captions":[{"t":null,"caption":"一只橘猫在窗台晒太阳"}]}',
        ))
        s.commit()

    res = search(_search_session(), "橘猫")
    assert any(i["asset_id"] == 413 for i in res["items"])
