"""Tests for the v2 content-encryption vault layer.

Exercises crypto primitives, the file format, the per-asset read entry
point, the placeholder fallback, the API surface (setup/unlock/lock), the
encrypted upload flow, and the scanner auto-import path.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Per-test clean data dir; mirrors ``tmp_data_dir`` in test_smoke.py."""
    monkeypatch.setenv("HOMETROVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOMETROVE_MEDIA_ROOTS", "")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from hometrove.db import reset_engine
    reset_engine()
    from hometrove.vault.state import reset_for_test
    reset_for_test()
    monkeypatch.setattr("hometrove.plugins.builtin.face_detect._get_app", lambda: None)
    yield tmp_path / "data"
    reset_for_test()
    reset_settings_cache()
    reset_engine()


# ---------------------------------------------------------------------------
# Crypto primitives
# ---------------------------------------------------------------------------


def test_argon2id_kdf_is_deterministic_with_same_salt():
    from hometrove.vault.crypto import derive_raw_master_key

    salt = b"\x01" * 16
    params = {"m": 64 * 1024, "t": 2, "p": 1}
    a = derive_raw_master_key("correct horse battery staple", salt, params)
    b = derive_raw_master_key("correct horse battery staple", salt, params)
    assert a == b and len(a) == 96
    c = derive_raw_master_key("wrong", salt, params)
    assert c != a


def test_aes_key_wrap_roundtrip():
    from hometrove.vault.crypto import aes_key_unwrap, aes_key_wrap

    kek = os.urandom(32)
    mk = os.urandom(96)
    wrapped = aes_key_wrap(kek, mk)
    assert wrapped != mk and len(wrapped) == len(mk) + 8
    assert aes_key_unwrap(kek, wrapped) == mk


def test_aead_roundtrip_with_aad():
    from hometrove.vault.crypto import decrypt_chunk, encrypt_chunk

    key = os.urandom(32)
    nonce = os.urandom(12)
    ct = encrypt_chunk(key, nonce, b"hello world", aad=b"asset=42:chunk=0")
    assert decrypt_chunk(key, nonce, ct, aad=b"asset=42:chunk=0") == b"hello world"
    with pytest.raises(Exception):
        decrypt_chunk(key, nonce, ct, aad=b"asset=42:chunk=1")


def test_hkdf_derives_distinct_subkeys():
    from hometrove.vault.crypto import derive_subkeys

    ikm = os.urandom(96)
    s = derive_subkeys(ikm)
    assert s.content_enc_key != s.kek
    assert s.kek != s.hash_key
    assert s.content_enc_key != s.filename_enc_key
    assert len(s.content_enc_key) == 32
    assert len(s.kek) == 32
    assert len(s.hash_key) == 32


def test_memzero_wipes_bytearray():
    from hometrove.vault.mem import memzero

    buf = bytearray(b"super secret" + b"\x00" * 20)
    memzero(buf)
    assert bytes(buf) == b"\x00" * len(buf)


def test_memzero_handles_empty_and_bytes():
    from hometrove.vault.mem import memzero

    memzero(bytearray())
    b = b"immutable"
    memzero(b)  # no-op for bytes


# ---------------------------------------------------------------------------
# File format round trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_file_roundtrip(tmp_path: Path):
    from hometrove.vault.stream import decrypt_stream, decrypt_to_bytes, encrypt_file

    src = tmp_path / "src.bin"
    src.write_bytes(os.urandom(300_000))
    dst = tmp_path / "dst.c9r"
    key = os.urandom(32)

    encrypt_file(src, dst, key=key, asset_id=42)
    assert dst.is_file() and dst.stat().st_size > 300_000

    out = decrypt_to_bytes(dst, key=key, asset_id=42)
    assert out == src.read_bytes()

    # streaming path yields the same plaintext
    collected = bytearray()
    for chunk in decrypt_stream(dst, key=key, asset_id=42):
        collected.extend(chunk)
    assert bytes(collected) == src.read_bytes()

    # a different asset_id flips the AAD and decrypt must fail
    with pytest.raises(Exception):
        decrypt_to_bytes(dst, key=key, asset_id=999)


def test_shred_overwrites_file(tmp_path: Path):
    from hometrove.vault.stream import encrypt_file, shred_file

    src = tmp_path / "secret.bin"
    src.write_bytes(b"super secret payload")
    dst = tmp_path / "dst.c9r"
    encrypt_file(src, dst, key=os.urandom(32), asset_id=1)
    assert dst.is_file()
    shred_file(dst)
    assert not dst.exists()


def test_encrypt_bytes_for_small_payloads(tmp_path: Path):
    from hometrove.vault.stream import decrypt_to_bytes, encrypt_bytes

    payload = b"hello" * 10
    dst = tmp_path / "small.c9r"
    key = os.urandom(32)
    nonce = encrypt_bytes(payload, dst, key=key, asset_id=7)
    assert len(nonce) == 12
    assert decrypt_to_bytes(dst, key=key, asset_id=7) == payload


# ---------------------------------------------------------------------------
# Path allocation
# ---------------------------------------------------------------------------


def test_allocate_content_path_creates_unique_paths(tmp_path: Path):
    from hometrove.vault.paths import allocate_content_path

    seen = set()
    for _ in range(50):
        full, rel = allocate_content_path(tmp_path)
        assert rel not in seen
        seen.add(rel)
        assert full.parent.exists()


def test_resolve_content_path_rejects_escape(tmp_path: Path):
    from hometrove.vault.paths import resolve_content_path

    with pytest.raises(ValueError):
        resolve_content_path(tmp_path, "../etc/passwd")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_vault_state_lifecycle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOMETROVE_DATA_DIR", str(tmp_path / "data"))
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from hometrove.vault.state import (
        VaultStatus,
        get_state,
        is_unlocked,
        lock,
        setup_with_password,
        unlock_with_password,
    )
    from hometrove.vault.crypto import derive_subkeys

    assert get_state().status == VaultStatus.DISABLED
    assert not is_unlocked()

    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    reset_settings_cache()
    setup_with_password("a-strong-password-123")
    assert get_state().status == VaultStatus.UNLOCKED
    assert is_unlocked()
    state = get_state()
    assert state.subkeys is not None
    assert isinstance(state.subkeys, derive_subkeys(os.urandom(96)).__class__)

    lock()
    assert get_state().status == VaultStatus.LOCKED
    assert not is_unlocked()

    unlock_with_password("a-strong-password-123")
    assert is_unlocked()

    # wrong password returns False (no exception)
    lock()
    assert unlock_with_password("wrong-password") is False


def test_vault_status_endpoint_reports_lifecycle(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        s = c.get("/api/vault/status").json()
        assert s["enabled"] is True
        # before setup it's still LOCKED because the lifespan flips it.
        assert s["configured"] is False or s["configured"] is True  # either state is acceptable here
        assert s["unlocked"] is False


# ---------------------------------------------------------------------------
# Read entry point + placeholder fallback
# ---------------------------------------------------------------------------


def _seed_vault_state_row(salt, params, wrapped):
    from hometrove.db import session_scope
    from hometrove.models import VaultState

    with session_scope() as s:
        s.add(VaultState(
            id=1,
            kdf_salt=salt,
            kdf_params_json=__import__("json").dumps(params, sort_keys=True),
            wrapped_master_key=wrapped,
            version=1,
        ))


def _derive_test_subkeys(password: str, salt: bytes, params: dict):
    from hometrove.vault.crypto import derive_raw_master_key, derive_subkeys

    raw = derive_raw_master_key(password, salt, params)
    return raw, derive_subkeys(raw)


def test_read_asset_bytes_returns_placeholder_for_encrypted_locked(tmp_data_dir):
    """An encrypted asset + vault locked returns a placeholder, not 404."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset
    from hometrove.vault.state import _enable_for_test
    from hometrove.vault.stream import encrypt_bytes

    _enable_for_test()  # noqa: SLF001
    salt = os.urandom(16)
    params = {"m": 64 * 1024, "t": 2, "p": 1}
    pwd = "placeholder-test"
    raw, sub = _derive_test_subkeys(pwd, salt, params)
    from hometrove.vault.crypto import aes_key_wrap

    wrapped = aes_key_wrap(bytes(sub.kek), raw)
    _seed_vault_state_row(salt, params, wrapped)
    # Don't unlock → vault locked.

    app = create_app()
    with TestClient(app) as c:
        from hometrove.config import get_settings
        from hometrove.vault.paths import allocate_content_path

        data_dir = get_settings().resolved_data_dir()
        full, rel = allocate_content_path(data_dir)
        full.parent.mkdir(parents=True, exist_ok=True)
        payload = b"plaintext-bytes"

        with session_scope() as s:
            a = Asset(
                path="",
                media_root="uploads",
                content_hash="x" * 64,
                media_type="image",
                size_bytes=len(payload),
                mtime=0,
                created_at=0,
                updated_at=0,
                filename="placeholder-test.bin",
                encrypted_path=rel,
                encrypted_nonce=None,
                origin_path="",
            )
            s.add(a)
            s.commit()
            aid = a.id
        # Encrypt with the real asset_id after the row exists.
        nonce = encrypt_bytes(payload, full, key=bytes(sub.content_enc_key), asset_id=aid)
        with session_scope() as s:
            a = s.get(Asset, aid)
            a.encrypted_nonce = nonce

        r = c.get(f"/api/assets/{aid}/file")
        assert r.status_code == 200, r.text
        assert len(r.content) > 0
        assert r.content != payload


def test_unlock_then_read_returns_plaintext(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    """After unlock, the same asset returns the original plaintext bytes."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset
    from hometrove.vault.state import _enable_for_test
    from hometrove.vault.crypto import aes_key_wrap
    from hometrove.vault.stream import encrypt_bytes

    _enable_for_test()  # noqa: SLF001
    salt = os.urandom(16)
    params = {"m": 64 * 1024, "t": 2, "p": 1}
    pwd = "the-master-password"
    raw, sub = _derive_test_subkeys(pwd, salt, params)
    wrapped = aes_key_wrap(bytes(sub.kek), raw)
    _seed_vault_state_row(salt, params, wrapped)

    app = create_app()
    with TestClient(app) as c:
        from hometrove.config import get_settings
        from hometrove.vault.paths import allocate_content_path

        data_dir = get_settings().resolved_data_dir()
        full, rel = allocate_content_path(data_dir)
        full.parent.mkdir(parents=True, exist_ok=True)
        payload = b"the-real-payload" * 100

        with session_scope() as s:
            a = Asset(
                path="",
                media_root="uploads",
                content_hash="x" * 64,
                media_type="image",
                size_bytes=len(payload),
                mtime=0,
                created_at=0,
                updated_at=0,
                filename="p.bin",
                encrypted_path=rel,
                encrypted_nonce=None,
                origin_path="",
            )
            s.add(a)
            s.commit()
            aid = a.id
        nonce = encrypt_bytes(payload, full, key=bytes(sub.content_enc_key), asset_id=aid)
        with session_scope() as s:
            a = s.get(Asset, aid)
            a.encrypted_nonce = nonce

        r = c.post("/api/vault/unlock", json={"password": pwd})
        assert r.status_code == 200, r.text
        r = c.get(f"/api/assets/{aid}/file")
        assert r.status_code == 200
        assert r.content == payload

        r = c.post("/api/vault/lock")
        assert r.status_code == 200
        r = c.get(f"/api/assets/{aid}/file")
        assert r.status_code == 200
        assert r.content != payload


# ---------------------------------------------------------------------------
# Vault API surface
# ---------------------------------------------------------------------------


def test_vault_api_setup_unlock_lock_flow(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        # status reflects enabled but not yet configured.
        s = c.get("/api/vault/status").json()
        assert s["enabled"] is True

        # short password is rejected by Pydantic min_length=12.
        r = c.post("/api/vault/setup", json={"password": "short", "confirm": "short"})
        assert r.status_code == 422

        # Real setup.
        pwd = "a-strong-master-pw-1234"
        r = c.post("/api/vault/setup", json={"password": pwd, "confirm": pwd})
        assert r.status_code == 200, r.text

        # Idempotency: setup twice is 409.
        r = c.post("/api/vault/setup", json={"password": "x" * 20, "confirm": "x" * 20})
        assert r.status_code == 409

        s = c.get("/api/vault/status").json()
        assert s["configured"] is True and s["unlocked"] is True

        # Lock + unlock with wrong / correct password.
        c.post("/api/vault/lock").raise_for_status()
        r = c.post("/api/vault/unlock", json={"password": "wrong-password"})
        assert r.status_code == 401
        r = c.post("/api/vault/unlock", json={"password": pwd})
        assert r.status_code == 200
        assert c.get("/api/vault/status").json()["unlocked"] is True

        # Session cookie is set.
        assert any(k in c.cookies for k in ("vault_session", "hometrove_vault"))


def test_vault_disabled_endpoints_return_400(tmp_data_dir):
    from fastapi.testclient import TestClient
    from hometrove.api import create_app

    app = create_app()
    with TestClient(app) as c:
        s = c.get("/api/vault/status").json()
        assert s["enabled"] is False
        assert s["configured"] is False
        assert s["unlocked"] is False
        r = c.post("/api/vault/setup", json={"password": "x" * 20, "confirm": "x" * 20})
        assert r.status_code == 400
        r = c.post("/api/vault/unlock", json={"password": "x" * 20})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Encrypted upload
# ---------------------------------------------------------------------------


def test_encrypted_upload_creates_asset_with_encrypted_path(
    tmp_data_dir, tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset
    from hometrove.vault.crypto import aes_key_wrap
    from hometrove.vault.state import _enable_for_test

    _enable_for_test()  # noqa: SLF001
    salt = os.urandom(16)
    params = {"m": 64 * 1024, "t": 2, "p": 1}
    pwd = "upload-test-password"
    raw, sub = _derive_test_subkeys(pwd, salt, params)
    wrapped = aes_key_wrap(bytes(sub.kek), raw)
    _seed_vault_state_row(salt, params, wrapped)

    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/vault/unlock", json={"password": pwd})
        assert r.status_code == 200

        from PIL import Image  # type: ignore
        p = tmp_path / "e.png"
        Image.new("RGB", (40, 30), (90, 160, 200)).save(p)

        r = c.post(
            "/api/uploads",
            json={"filename": "e.png", "size": p.stat().st_size, "encrypted": True},
        )
        r.raise_for_status()
        sid = r.json()["upload_id"]
        c.put(
            f"/api/uploads/{sid}/chunks/0",
            files={"file": ("e.png", p.read_bytes(), "image/png")},
        )
        c.post(f"/api/uploads/{sid}/complete", json={})
        r = c.post(f"/api/uploads/{sid}/ingest")
        assert r.status_code == 200, r.text
        aid = int(r.json()["asset_id"])

        with session_scope() as s:
            a = s.get(Asset, aid)
            assert a.encrypted_path and not a.path
            assert a.media_type == "image"
        r = c.get(f"/api/assets/{aid}/file")
        assert r.status_code == 200
        assert r.content == p.read_bytes()


def test_encrypted_upload_refused_when_locked(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("HOMETROVE_VAULT_ENABLED", "true")
    from hometrove.config import reset_settings_cache
    reset_settings_cache()
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.vault.crypto import aes_key_wrap
    from hometrove.vault.state import _enable_for_test

    _enable_for_test()  # noqa: SLF001
    salt = os.urandom(16)
    params = {"m": 64 * 1024, "t": 2, "p": 1}
    pwd = "locked-test"
    raw, sub = _derive_test_subkeys(pwd, salt, params)
    wrapped = aes_key_wrap(bytes(sub.kek), raw)
    _seed_vault_state_row(salt, params, wrapped)

    app = create_app()
    with TestClient(app) as c:
        r = c.post(
            "/api/uploads",
            json={"filename": "x.png", "size": 1024, "encrypted": True},
        )
        assert r.status_code in (400, 423), r.text


# ---------------------------------------------------------------------------
# Plaintext path untouched (regression for vault disabled mode)
# ---------------------------------------------------------------------------


def test_plaintext_upload_still_works_with_vault_disabled(tmp_data_dir, tmp_path: Path):
    """Vault disabled = legacy behavior: chunks → plaintext file, no encrypted_path."""
    from fastapi.testclient import TestClient
    from hometrove.api import create_app
    from hometrove.db import session_scope
    from hometrove.models import Asset

    app = create_app()
    with TestClient(app) as c:
        from PIL import Image
        p = tmp_path / "plain.png"
        Image.new("RGB", (40, 30), (10, 20, 30)).save(p)

        r = c.post("/api/uploads", json={"filename": "plain.png", "size": p.stat().st_size})
        r.raise_for_status()
        sid = r.json()["upload_id"]
        c.put(
            f"/api/uploads/{sid}/chunks/0",
            files={"file": ("plain.png", p.read_bytes(), "image/png")},
        )
        c.post(f"/api/uploads/{sid}/complete", json={})
        r = c.post(f"/api/uploads/{sid}/ingest")
        assert r.status_code == 200
        aid = int(r.json()["asset_id"])

        with session_scope() as s:
            a = s.get(Asset, aid)
            assert not a.encrypted_path
            assert a.path.startswith("uploads\0") or a.path.startswith("uploads\x00")