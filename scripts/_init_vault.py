"""Vault setup helper called from scripts/init.bat.

Reads a single line from stdin (the master password), initialises the vault
singleton in-memory, and persists the wrapped key to the ``vault_state``
table. Exits 0 on success, non-zero on failure. The password is held only
in a local variable and never logged.
"""

from __future__ import annotations

import json
import sys
import time


def main() -> int:
    pw = sys.stdin.readline().rstrip("\r\n")
    if len(pw) < 12:
        print("error: master password must be at least 12 characters", file=sys.stderr)
        return 2

    from hometrove.config import get_settings
    settings = get_settings()
    if not settings.vault_enabled:
        print("error: vault is disabled (set HOMETROVE_VAULT_ENABLED=true)", file=sys.stderr)
        return 2

    from hometrove.db import session_scope
    from hometrove.models import VaultState
    from hometrove.vault.state import get_state, setup_with_password

    with session_scope() as s:
        existing = s.get(VaultState, 1)
        if existing is not None:
            print("error: vault already initialised", file=sys.stderr)
            return 3

    setup_with_password(pw)
    st = get_state()

    with session_scope() as s:
        s.add(
            VaultState(
                id=1,
                kdf_salt=st.kdf_salt,
                kdf_params_json=json.dumps(st.kdf_params, sort_keys=True),
                wrapped_master_key=st.wrapped_master_key,
                version=1,
                created_at=int(time.time()),
                updated_at=int(time.time()),
            )
        )

    print("vault initialised and unlocked for the lifetime of this process")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())