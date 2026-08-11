"""Vault status / setup / unlock / lock endpoints.

These endpoints are the only HTTP surface that mutates the in-memory
vault state.  ``/api/vault/status`` is safe to call at any time; the
other three require ``vault_enabled=true`` in settings.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from hometrove.config import get_settings
from hometrove.db import session_scope
from hometrove.models import Asset, VaultState
from hometrove.vault import is_unlocked
from hometrove.vault.state import (
    VaultStatus,
    get_state,
    lock,
    session_token_b64,
    setup_with_password,
    unlock_from_session,
    unlock_with_password,
)

log = logging.getLogger("hometrove.api.vault")

router = APIRouter(prefix="/api/vault", tags=["vault"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class VaultStatusPayload(BaseModel):
    enabled: bool
    configured: bool
    unlocked: bool
    total_assets: Optional[int] = None
    encrypted_assets: Optional[int] = None


class SetupPayload(BaseModel):
    password: str = Field(min_length=12, max_length=512)
    confirm: str = Field(min_length=12, max_length=512)


class UnlockPayload(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def _set_cookie(response: Response, token: str, ttl_seconds: int) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.vault_cookie_name,
        value=token,
        max_age=ttl_seconds,
        httponly=True,
        samesite="strict",
        path="/",
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/status", response_model=VaultStatusPayload)
async def vault_status() -> VaultStatusPayload:
    """Return the current vault lifecycle state and asset counts."""

    settings = get_settings()
    state = get_state()
    payload = VaultStatusPayload(
        enabled=settings.vault_enabled,
        configured=state.status not in (VaultStatus.DISABLED, VaultStatus.INITIALIZED),
        unlocked=state.status == VaultStatus.UNLOCKED,
    )
    if not settings.vault_enabled:
        return payload
    try:
        with session_scope() as s:
            total = s.execute(select(func.count(Asset.id))).scalar_one()
            encrypted = s.execute(
                select(func.count(Asset.id)).where(Asset.encrypted_path.is_not(None))
            ).scalar_one()
    except Exception:  # noqa: BLE001 — DB not ready at startup
        total = None
        encrypted = None
    payload.total_assets = total
    payload.encrypted_assets = encrypted
    return payload


@router.post("/setup")
async def setup_vault(body: SetupPayload, response: Response) -> dict:
    """Initialise a brand-new vault with ``body.password``.

    Persists the wrapped master key in the ``vault_state`` singleton row
    and flips the in-memory status to ``UNLOCKED``.
    """

    settings = get_settings()
    if not settings.vault_enabled:
        raise HTTPException(status_code=400, detail="vault is not enabled")
    if body.password != body.confirm:
        raise HTTPException(status_code=400, detail="passwords do not match")

    # Check whether a vault row already exists.
    with session_scope() as s:
        existing = s.get(VaultState, 1)
    if existing is not None:
        raise HTTPException(status_code=409, detail="vault already initialised")

    setup_with_password(body.password)

    state = get_state()
    import time

    with session_scope() as s:
        s.add(
            VaultState(
                id=1,
                kdf_salt=state.kdf_salt,
                kdf_params_json=__import__("json").dumps(state.kdf_params, sort_keys=True),
                wrapped_master_key=state.wrapped_master_key,
                version=1,
                created_at=int(time.time()),
                updated_at=int(time.time()),
            )
        )
    token = session_token_b64()
    if token:
        _set_cookie(response, token, settings.vault_session_ttl_seconds)
    return {"ok": True, "unlocked": True}


@router.post("/unlock")
async def unlock_vault(body: UnlockPayload, response: Response) -> dict:
    """Derive the master key from ``body.password`` and unlock the vault."""

    settings = get_settings()
    if not settings.vault_enabled:
        raise HTTPException(status_code=400, detail="vault is not enabled")

    # Hydrate the cached kdf params + wrapped key from the DB if they
    # aren't already in memory (e.g. after a fresh process restart).
    state = get_state()
    if not state.kdf_salt or not state.wrapped_master_key:
        with session_scope() as s:
            row = s.get(VaultState, 1)
        if row is None:
            raise HTTPException(status_code=404, detail="vault not initialised")
        from hometrove.vault import crypto as vault_crypto

        state.kdf_salt = row.kdf_salt
        state.kdf_params = vault_crypto.kdf_params_from_json(row.kdf_params_json)
        state.wrapped_master_key = row.wrapped_master_key
        state.status = VaultStatus.LOCKED

    ok = unlock_with_password(body.password)
    if not ok:
        raise HTTPException(status_code=401, detail="invalid master password")
    token = session_token_b64()
    if token:
        _set_cookie(response, token, settings.vault_session_ttl_seconds)
    return {"ok": True, "unlocked": True}


@router.post("/lock")
async def lock_vault(response: Response) -> dict:
    """Zero in-memory key material and clear the unlock cookie."""

    settings = get_settings()
    lock()
    response.delete_cookie(settings.vault_cookie_name, path="/")
    return {"ok": True, "unlocked": False}


# ---------------------------------------------------------------------------
# Helpers used by the middleware
# ---------------------------------------------------------------------------


def try_resume_session(token: str | None) -> bool:
    """Used by ``VaultSessionMiddleware`` to rehydrate the unlock status."""

    return unlock_from_session(token)


def get_unlocked() -> bool:
    return is_unlocked()


# Re-export so tests can import without depending on internal state.
__all__ = [
    "router",
    "try_resume_session",
    "get_unlocked",
]