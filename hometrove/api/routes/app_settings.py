"""App-wide settings endpoints.

A single ``GET /api/settings`` + ``PUT /api/settings`` covers today's
tiny surface (just the global encryption toggle). The shape is
deliberately generic so adding more flags won't require new paths.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hometrove.app_settings import (
    SETTING_ENCRYPT_NEW_UPLOADS,
    is_encrypt_new_uploads_enabled,
    set_encrypt_new_uploads_enabled,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsDTO(BaseModel):
    """Persisted app-wide settings.

    The vault-related fields are derived (not persisted) and let the
    frontend decide whether to route the user to setup, unlock, or
    just enable the toggle directly.
    """

    encrypt_new_uploads: bool = False
    # True when the vault subsystem is live (env var HOMETROVE_VAULT_ENABLED).
    vault_enabled: bool = True
    # True when the master password has been set (LOCKED or UNLOCKED).
    # The toggle's PUT endpoint rejects ``encrypt_new_uploads=true`` when
    # this is false.
    vault_configured: bool = False
    # True when the process is currently unlocked. The frontend uses
    # this to decide whether to nudge the user to unlock before
    # kicking off an upload.
    vault_unlocked: bool = False


class UpdateSettingsBody(BaseModel):
    encrypt_new_uploads: bool = Field(..., description="Force encryption on new uploads.")


def _status_payload() -> SettingsDTO:
    """Build the response payload, including derived vault flags."""
    from hometrove.config import get_settings
    from hometrove.vault.state import (
        has_master_password as _vault_has_master,
        is_unlocked as _vault_unlocked,
    )

    settings = get_settings()
    return SettingsDTO(
        encrypt_new_uploads=is_encrypt_new_uploads_enabled(),
        vault_enabled=settings.vault_enabled,
        vault_configured=_vault_has_master(),
        vault_unlocked=_vault_unlocked(),
    )


@router.get("", response_model=SettingsDTO)
def get_settings_endpoint() -> SettingsDTO:
    return _status_payload()


@router.put("", response_model=SettingsDTO)
def update_settings_endpoint(body: UpdateSettingsBody) -> SettingsDTO:
    """Persist the toggle. The frontend uses the response to refresh UI."""
    from fastapi import HTTPException

    from hometrove.config import get_settings
    from hometrove.vault.state import has_master_password as _vault_has_master

    settings = get_settings()
    if body.encrypt_new_uploads and not settings.vault_enabled:
        raise HTTPException(400, "vault is disabled via HOMETROVE_VAULT_ENABLED; cannot enable encryption")
    if body.encrypt_new_uploads and not _vault_has_master():
        # Refuse to flip the toggle into a state where the next upload
        # would 423. The UI is expected to route the user to setup first.
        raise HTTPException(
            409,
            "vault is not configured yet; complete setup before enabling encryption",
        )
    set_encrypt_new_uploads_enabled(body.encrypt_new_uploads)
    return _status_payload()
