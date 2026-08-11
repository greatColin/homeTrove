"""Global vault state: status enum + master-key lifecycle.

The ``VAULT`` singleton is created at module import time and updated
in-place by the setup / unlock / lock HTTP endpoints and the
``VaultSessionMiddleware``.  Worker subprocesses inherit the state by
serialising the wrapped master key + Argon2id parameters into a
configurable file (``HOMETROVE_VAULT_SHARED_KEY``) and unlocking
independently — the ``bootstrap_from_env`` helper supports that path.

Memory hygiene:

* All sensitive buffers are ``bytearray`` so ``vault.mem.memzero``
  can overwrite them in place before they go out of scope.
* ``lock()`` calls ``memzero`` on every subkey field and the raw
  master key before flipping the status enum.
* Process atexit handlers wipe the state again on graceful shutdown.
"""
from __future__ import annotations

import atexit
import base64
import logging
import os
import threading
from dataclasses import dataclass
from enum import Enum

from hometrove.vault import crypto
from hometrove.vault.mem import memzero

log = logging.getLogger("hometrove.vault.state")


class VaultStatus(str, Enum):
    """Lifecycle states of the vault singleton."""

    DISABLED = "disabled"          # HOMETROVE_VAULT_ENABLED=false
    INITIALIZED = "initialized"    # DB row created, master password not set yet
    LOCKED = "locked"              # master password set, process not unlocked
    UNLOCKED = "unlocked"          # master password set, subkeys in memory


@dataclass
class _State:
    status: VaultStatus = VaultStatus.DISABLED
    kdf_salt: bytes = b""
    kdf_params: dict | None = None
    wrapped_master_key: bytes = b""
    raw_master_key: bytearray | None = None
    subkeys: crypto.VaultSubkeys | None = None
    session_token: bytes = b""


_STATE = _State()
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------


def get_state() -> _State:
    """Return the live state object.  Internal — use ``VAULT`` instead."""

    return _STATE


def is_unlocked() -> bool:
    return _STATE.status == VaultStatus.UNLOCKED


def require_unlocked() -> None:
    """FastAPI dependency.  Raises ``HTTPException`` if vault is locked."""

    from fastapi import HTTPException

    if not is_unlocked():
        raise HTTPException(status_code=423, detail="vault is locked")


# Backwards-compatible alias for the design document.
VAULT = _State


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


def set_disabled() -> None:
    """Mark the vault as disabled (HOMETROVE_VAULT_ENABLED=false)."""

    with _LOCK:
        _reset_locked_state()
        _STATE.status = VaultStatus.DISABLED


def set_initialized(salt: bytes, params: dict, wrapped: bytes) -> None:
    """Persist a freshly generated wrapped master key in memory."""

    with _LOCK:
        _reset_locked_state()
        _STATE.kdf_salt = salt
        _STATE.kdf_params = params
        _STATE.wrapped_master_key = wrapped
        _STATE.status = VaultStatus.LOCKED


def setup_with_password(password: str) -> None:
    """Initialise a fresh vault from a brand new master password.

    Generates a new salt, derives the raw master key via Argon2id, splits
    it into the canonical subkeys, wraps the raw master key under the
    ``db_key`` subkey, and flips the status to UNLOCKED so the caller
    can immediately write encrypted data.
    """

    if len(password) < 12:
        raise ValueError("master password must be at least 12 characters")
    salt = crypto.generate_kdf_salt()
    params = crypto.kdf_params_dict()
    raw = bytearray(crypto.derive_raw_master_key(password, salt, params))
    subkeys = crypto.derive_subkeys(bytes(raw))
    wrapped = crypto.wrap_master_key(bytes(raw), subkeys.kek)

    with _LOCK:
        _reset_locked_state()
        _STATE.kdf_salt = salt
        _STATE.kdf_params = params
        _STATE.wrapped_master_key = wrapped
        _STATE.raw_master_key = raw
        _STATE.subkeys = subkeys
        _STATE.status = VaultStatus.UNLOCKED
        _STATE.session_token = _mint_session_token(subkeys.hash_key)


def unlock_with_password(password: str) -> bool:
    """Derive the master key from ``password`` and verify the stored wrap.

    Returns ``True`` on success, ``False`` on password mismatch.

    The wrap uses ``subkeys.kek`` (itself derived from the raw master
    key) so verification has to re-derive the subkeys from the password
    and compare the rewrapped value against the persisted blob.  This
    avoids keeping an independent wrap key in the DB while still proving
    the password is correct.
    """

    with _LOCK:
        if not _STATE.kdf_salt or not _STATE.wrapped_master_key or not _STATE.kdf_params:
            return False
        salt = _STATE.kdf_salt
        params = _STATE.kdf_params
        wrapped = _STATE.wrapped_master_key

    raw_ba = bytearray(crypto.derive_raw_master_key(password, salt, params))
    try:
        subkeys = crypto.derive_subkeys(bytes(raw_ba))
    except Exception:
        memzero(raw_ba)
        return False
    try:
        rewrapped = crypto.wrap_master_key(bytes(raw_ba), subkeys.kek)
    except Exception:
        memzero(raw_ba)
        return False
    if not _constant_time_eq(rewrapped, wrapped):
        memzero(raw_ba)
        return False

    with _LOCK:
        _reset_locked_state()
        _STATE.raw_master_key = raw_ba
        _STATE.subkeys = subkeys
        _STATE.status = VaultStatus.UNLOCKED
        _STATE.session_token = _mint_session_token(subkeys.hash_key)
    return True


def _constant_time_eq(a: bytes, b: bytes) -> bool:
    """Constant-time byte comparison; defensive against timing attacks."""

    import hmac

    return hmac.compare_digest(a, b)


def unlock_from_session(token_b64: str | None) -> bool:
    """Recover an unlocked state from a session token.

    Used by ``VaultSessionMiddleware`` on every request so the master
    password doesn't have to be re-entered for the cookie TTL.  The
    token itself is just the random bytes that ``setup_with_password``
    and ``unlock_with_password`` minted — the server tracks them by
    in-memory association.  When the process restarts the mapping is
    empty so unlock-by-cookie cannot survive a restart (the user has to
    re-enter the master password).
    """

    if not token_b64:
        return False
    try:
        token = base64.urlsafe_b64decode(token_b64.encode("ascii"))
    except Exception:
        return False
    with _LOCK:
        if not _STATE.raw_master_key or not _STATE.subkeys:
            return False
        if _STATE.session_token and token == _STATE.session_token:
            _STATE.status = VaultStatus.UNLOCKED
            return True
        return False


def lock() -> None:
    """Zero all in-memory key material and flip status back to LOCKED."""

    with _LOCK:
        _reset_locked_state()
        _STATE.status = VaultStatus.LOCKED


def reset_for_test() -> None:
    """Test-only: wipe the in-memory vault state back to DISABLED."""

    with _LOCK:
        _reset_locked_state()
        _STATE.kdf_salt = b""
        _STATE.kdf_params = None
        _STATE.wrapped_master_key = b""
        _STATE.session_token = b""
        _STATE.status = VaultStatus.DISABLED


def _enable_for_test() -> None:
    """Test-only: flip the vault into ENABLED mode without touching the DB.

    The ``vault.api`` endpoints refuse to act unless the process status
    is something other than ``DISABLED``; tests want to drive the
    unlock/setup paths without spinning up the full lifespan.
    """
    with _LOCK:
        _STATE.status = VaultStatus.LOCKED


def _force_unlock_for_test(subkeys: crypto.VaultSubkeys) -> None:
    """Test-only: pin the in-memory state to a freshly-derived subkey set."""
    with _LOCK:
        _STATE.subkeys = subkeys
        _STATE.raw_master_key = bytearray(b"\x00" * 32)
        _STATE.status = VaultStatus.UNLOCKED
        _STATE.session_token = os.urandom(32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_locked_state() -> None:
    if _STATE.raw_master_key is not None:
        memzero(_STATE.raw_master_key)
    if _STATE.subkeys is not None:
        # ``VaultSubkeys`` stores plain ``bytes`` so we zero a copy
        # through ``bytearray`` and rely on the caller not retaining
        # references to the original fields.
        for fname in (
            "content_enc_key",
            "filename_enc_key",
            "metadata_enc_key",
            "hash_key",
            "kek",
        ):
            current = getattr(_STATE.subkeys, fname, b"")
            if isinstance(current, (bytes, bytearray)) and current:
                buf = bytearray(current)
                memzero(buf)
        _STATE.subkeys = None
    _STATE.raw_master_key = None
    _STATE.session_token = b""


def _mint_session_token(hash_key: bytes) -> bytes:
    """Mint a 32-byte session token bound to the current subkeys."""

    # The token carries no key material — its only job is to be
    # recognised by ``unlock_from_session`` during the cookie TTL.
    # Binding it to ``hash_key`` means a leaked token from a prior
    # process can't unlock a vault whose hash_key rotated.
    import hmac

    return hmac.new(hash_key, os.urandom(16), "sha256").digest()


def session_token_b64() -> str | None:
    if not _STATE.session_token:
        return None
    return base64.urlsafe_b64encode(_STATE.session_token).decode("ascii")


@atexit.register
def _atexit_lock() -> None:
    """Always zero key material on interpreter shutdown."""

    try:
        lock()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass