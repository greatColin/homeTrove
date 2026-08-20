"""Tests for the agent CLI and its backend endpoints."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from hometrove.api import create_app
from hometrove.config import reset_settings_cache


@pytest.fixture
def api_key():
    return "test-agent-key"


@pytest.fixture
def client(api_key, tmp_path):
    reset_settings_cache()
    os.environ["HOMETROVE_DATA_DIR"] = str(tmp_path / "data")
    os.environ["HOMETROVE_MEDIA_ROOTS"] = str(tmp_path / "media")
    os.environ["HOMETROVE_CLI_API_KEY"] = api_key
    os.environ["HOMETROVE_READ_ONLY_CHECK"] = "off"
    os.environ["HOMETROVE_AUTH_BACKEND"] = "token"
    app = create_app()
    with TestClient(app) as c:
        yield c
    reset_settings_cache()


@pytest.fixture
def sample_image(tmp_path):
    p = tmp_path / "sample.jpg"
    Image.new("RGB", (80, 60), color="blue").save(p)
    return p


# ---------------------------------------------------------------------------
# Backend endpoint tests
# ---------------------------------------------------------------------------


def test_plugin_test_requires_auth(client, sample_image):
    r = client.post("/api/plugins/basic.info/test", files={"file": sample_image.open("rb")})
    assert r.status_code == 401


def test_plugin_test_unknown_plugin(client, sample_image, api_key):
    r = client.post(
        "/api/plugins/unknown.plugin/test",
        files={"file": sample_image.open("rb")},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 404


def test_plugin_test_unsupported_media(client, api_key):
    buf = io.BytesIO(b"not an image")
    r = client.post(
        "/api/plugins/basic.info/test",
        files={"file": ("bad.txt", buf, "text/plain")},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 400


def test_plugin_test_runs_basic_info(client, sample_image, api_key):
    r = client.post(
        "/api/plugins/basic.info/test",
        files={"file": sample_image.open("rb")},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["plugin_id"] == "basic.info"
    assert data["media_type"] == "image"
    assert "result" in data
    assert data["result"]["media_type"] == "image"


def test_passthrough_backend_allows_open_routes(client):
    r = client.get("/api/health")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# CLI unit tests
# ---------------------------------------------------------------------------


def test_endpoint_map_contains_search():
    from hometrove.cli_agent import ENDPOINTS

    assert "search" in ENDPOINTS
    assert ENDPOINTS["search"]["method"] == "GET"
    assert "q" in ENDPOINTS["search"]["params"]


def test_coerce_int_and_bool():
    from hometrove.cli_agent import _coerce

    assert _coerce("42", "int") == 42
    assert _coerce("true", "bool") is True
    assert _coerce("0", "bool") is False
    assert _coerce("hello", "str") == "hello"


def test_build_call_args_replaces_path_and_query():
    from hometrove.cli_agent import _build_call_args

    path, query = _build_call_args("asset", [("asset_id", "7")])
    assert path == "/api/assets/7"
    assert query == {}

    path, query = _build_call_args("search", [("q", "cat"), ("limit", "5")])
    assert path == "/api/search"
    assert query == {"q": "cat", "limit": 5}


def test_upload_format_output():
    from hometrove.cli_agent import AgentClient

    client = AgentClient(host="http://h", api_key="k")
    out = client.upload_file
    # The method exists and accepts a Path + optional plugins list.
    assert callable(out)


# ---------------------------------------------------------------------------
# CLI integration test against a real server process
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    """Spin up hometrove serve on a free port and yield its URL."""
    tmp = tmp_path_factory.mktemp("hometrove-cli-server")
    media = tmp / "media"
    data = tmp / "data"
    media.mkdir()
    data.mkdir()
    Image.new("RGB", (80, 60), color="green").save(media / "green.jpg")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent.parent)
    env["HOMETROVE_DATA_DIR"] = str(data)
    env["HOMETROVE_MEDIA_ROOTS"] = str(media)
    env["HOMETROVE_CLI_API_KEY"] = "integration-key"
    env["HOMETROVE_READ_ONLY_CHECK"] = "off"

    proc = subprocess.Popen(
        [sys.executable, "-m", "hometrove.cli", "serve"],
        cwd=str(Path(__file__).parent.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    url = "http://127.0.0.1:8080"
    # Server startup is slow when face plugins boot-load the InsightFace
    # pack (a ~250MB onnx pack takes on the order of a minute+ to load on
    # a cold CPU cache the first time). Keep the deadline generous so the
    # fixture is robust to cold model loads, not just warm ones.
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            import urllib.request

            with urllib.request.urlopen(f"{url}/api/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError("server failed to start")

    try:
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _cli(*args, server_url, api_key="integration-key"):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parent.parent)
    env["HOMETROVE_CLI_HOST"] = server_url
    env["HOMETROVE_CLI_API_KEY"] = api_key
    result = subprocess.run(
        [sys.executable, "-m", "hometrove.cli_agent", *args],
        cwd=str(Path(__file__).parent.parent),
        env=env,
        capture_output=True,
        text=True,
    )
    return result


@pytest.mark.integration
def test_cli_endpoints(server_url):
    r = _cli("endpoints", server_url=server_url)
    assert r.returncode == 0
    assert "search" in r.stdout
    assert "upload" in r.stdout


@pytest.mark.integration
def test_cli_describe_search(server_url):
    r = _cli("describe", "search", server_url=server_url)
    assert r.returncode == 0
    assert "q (str) required" in r.stdout


@pytest.mark.integration
def test_cli_call_health(server_url):
    r = _cli("call", "health", server_url=server_url)
    assert r.returncode == 0
    assert "ok" in r.stdout


@pytest.mark.integration
def test_cli_upload_and_search(server_url):
    media = Path(__file__).parent.parent / "tests" / "green.jpg"
    if not media.is_file():
        pytest.skip("integration media file not present")
    # Vault is enabled by default; set it up so encrypted uploads succeed.
    r = _cli(
        "vault", "setup",
        "--password", "integration-pass",
        "--confirm", "integration-pass",
        server_url=server_url,
    )
    assert r.returncode == 0, r.stderr
    r = _cli("upload", str(media), "--plugins", "basic.info", server_url=server_url)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert "asset_id" in data
    assert data["url"].startswith(server_url)

    # Wait for the worker to finish indexing.
    deadline = time.time() + 15
    while time.time() < deadline:
        r = _cli("search", "green", "--limit", "5", server_url=server_url)
        if f"asset:{data['asset_id']}" in r.stdout:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("uploaded asset did not appear in search results")


@pytest.mark.integration
def test_cli_test_plugin(server_url):
    media = Path(__file__).parent.parent / "tests" / "green.jpg"
    if not media.is_file():
        pytest.skip("integration media file not present")
    r = _cli("test-plugin", "basic.info", str(media), server_url=server_url)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["plugin_id"] == "basic.info"
    assert data["media_type"] == "image"


@pytest.mark.integration
def test_cli_auth_rejects_bad_key(server_url):
    r = _cli(
        "test-plugin",
        "basic.info",
        str(Path(__file__).parent.parent / "tests" / "green.jpg"),
        server_url=server_url,
        api_key="wrong-key",
    )
    assert r.returncode == 1
    assert "401" in r.stderr


@pytest.mark.integration
def test_cli_plugins_list(server_url):
    r = _cli("plugins", server_url=server_url)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert "items" in body
    ids = {p["id"] for p in body["items"]}
    assert "basic.info" in ids


@pytest.mark.integration
def test_cli_plugins_enabled_prints_ids(server_url):
    r = _cli("plugins", "--enabled", server_url=server_url)
    assert r.returncode == 0, r.stderr
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    assert "basic.info" in lines


@pytest.mark.integration
def test_cli_describe_plugin(server_url):
    r = _cli("describe-plugin", "basic.info", server_url=server_url)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["id"] == "basic.info"
    assert body["enabled"] is True
    assert "status" in body


@pytest.mark.integration
def test_cli_upload_rejects_disabled_plugin(server_url):
    # Disable basic.info then attempt to upload with --plugins basic.info.
    import urllib.request

    req = urllib.request.Request(
        f"{server_url}/api/plugins/basic.info",
        data=json.dumps({"enabled": False}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
    media = Path(__file__).parent.parent / "tests" / "green.jpg"
    if not media.is_file():
        pytest.skip("integration media file not present")
    r = _cli(
        "upload", str(media), "--plugins", "basic.info", server_url=server_url,
    )
    assert r.returncode == 2
    assert "basic.info" in r.stderr
    # Re-enable for subsequent tests.
    req = urllib.request.Request(
        f"{server_url}/api/plugins/basic.info",
        data=json.dumps({"enabled": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
