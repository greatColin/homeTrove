"""homeTrove vault: at-rest content encryption.

The vault module owns the encryption primitives, the in-memory master
key state, the file path layout, the streaming encrypt / decrypt
routines, and the single read entry point that the HTTP layer and
plugin worker use to fetch asset bytes regardless of whether the asset
is stored in plaintext or encrypted form.

Module map:

* ``crypto`` — AES-256-GCM, Argon2id, HKDF, AES-Key-Wrap helpers.
* ``state`` — global ``VAULT`` singleton + status enum + memory-safe
  key buffer handling.
* ``paths`` — vault directory layout + placeholder paths.
* ``stream`` — streaming encrypt / decrypt + Range support + secure
  shred helper.
* ``read`` — single ``read_asset_bytes`` / ``read_asset_stream`` entry.
* ``placeholders`` — placeholder resource generation.
"""
from __future__ import annotations

from hometrove.vault.state import VaultStatus, VAULT, is_unlocked, require_unlocked

__all__ = [
    "VaultStatus",
    "VAULT",
    "is_unlocked",
    "require_unlocked",
]