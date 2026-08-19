"""Persisted app-wide settings (user-toggled, DB-backed).

Today only one setting lives here — the global encryption toggle
``encrypt_new_uploads``. When enabled, every new upload is encrypted
under the configured vault. The flag is independent of the vault's
configured/unlocked state: if the vault is configured but locked, the
upload is rejected with a clear error so the user can unlock first.

The toggle is intentionally distinct from ``vault_enabled`` (env var,
process-wide) and the ``vault_state`` row (whether the vault is
configured at all). Three orthogonal knobs:

* ``vault_enabled`` env var — disable the entire vault subsystem.
* ``vault_state`` row — whether the master key is wrapped on disk.
* ``encrypt_new_uploads`` row — whether new uploads ride the vault.

Reads/writes go through ``get_setting`` / ``set_setting`` so the
SQL plumbing lives in one place.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from .db import session_scope
from .models import AppSetting

SETTING_ENCRYPT_NEW_UPLOADS = "encrypt_new_uploads"

_KNOWN_SETTINGS: frozenset[str] = frozenset({SETTING_ENCRYPT_NEW_UPLOADS})


def _now() -> int:
    """Unix seconds; mirrors ``models._now`` without exposing internals."""
    import time

    return int(time.time())


def get_setting(key: str) -> Optional[str]:
    """Return the raw string value of ``key`` or ``None`` if unset."""
    with session_scope() as s:
        row = s.get(AppSetting, key)
        return row.value if row is not None else None


def set_setting(key: str, value: str) -> None:
    """Upsert ``key`` to ``value``. Caller is responsible for validation."""
    if key not in _KNOWN_SETTINGS:
        # Refuse silently unknown keys so a typo in a call site doesn't
        # pollute the table; the typed wrapper APIs below validate first.
        raise ValueError(f"unknown setting: {key!r}")
    now = _now()
    with session_scope() as s:
        row = s.get(AppSetting, key)
        if row is None:
            s.add(AppSetting(key=key, value=value, updated_at=now))
        else:
            row.value = value
            row.updated_at = now


def is_encrypt_new_uploads_enabled() -> bool:
    """Return whether the global encryption toggle is on."""
    v = get_setting(SETTING_ENCRYPT_NEW_UPLOADS)
    if v is None:
        return False
    return v.strip().lower() in ("1", "true", "yes", "on")


def set_encrypt_new_uploads_enabled(enabled: bool) -> None:
    """Persist the toggle in canonical ``"true"`` / ``"false"`` form."""
    set_setting(SETTING_ENCRYPT_NEW_UPLOADS, "true" if enabled else "false")


def list_known_settings() -> dict[str, str]:
    """Return all known settings as a dict (used by the API endpoint)."""
    with session_scope() as s:
        rows = s.execute(select(AppSetting).where(AppSetting.key.in_(_KNOWN_SETTINGS))).scalars().all()
    return {r.key: r.value for r in rows}
