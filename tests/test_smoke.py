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
    # In unit tests we exercise the mock face pipeline (``mock.faces`` ->
    # ``face.match``), so simulate "insightface unavailable" to force
    # ``face.detect`` to skip and let ``face.match`` fall back to the mocks.
    monkeypatch.setattr("hometrove.plugins.builtin.face_detect._get_app", lambda: None)
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
        assert enqueue_pending(s) == n * len(REGISTRY.list())
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
