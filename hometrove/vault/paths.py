"""Vault directory layout: content / thumbnail / keyframe paths.

Layout under ``{data_dir}/vault/``::

    v/                      # encrypted original content
        HH/HH/<32hex>.c9r   # 2-level fan-out via BLAKE3(secret).hexdigest()
    t/<asset_id>/<size>.c9r       # encrypted thumbnails
    k/<asset_id>/<scene>-<idx>.c9r  # encrypted keyframes

The ``.c9r`` extension is a homeTrove marker for "vault-encrypted
file"; it is not interpreted by the OS but it makes the directory
listing self-explanatory.
"""
from __future__ import annotations

import hashlib
import secrets
from pathlib import Path


_VAULT_DIRNAME = "vault"
_PLACEHOLDERS_DIRNAME = "placeholders"

#: Recognised thumbnail size buckets (mirrors the thumbnail plugin's output).
_THUMB_SIZES = frozenset({"small", "medium", "placeholder"})

#: Bytes of randomness in a vault content filename.  32 hex characters
#: = 128 bits of entropy, well above the AES-256 key size.
_CONTENT_NAME_BYTES = 16


def vault_dir(data_dir: Path) -> Path:
    """Return the vault root directory (created on demand)."""

    p = data_dir / _VAULT_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def placeholders_dir(data_dir: Path) -> Path:
    """Return the placeholder assets directory (created on demand)."""

    p = data_dir / _PLACEHOLDERS_DIRNAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def allocate_content_path(data_dir: Path) -> tuple[Path, str]:
    """Return ``(absolute_path, vault_relative_path)`` for a new content slot.

    The returned path lives under ``vault/v/HH/HH/<random>.c9r`` where
    the directory fan-out is a BLAKE3-derived slice of a fresh random
    identifier — the fan-out is uniform across the two-byte hex prefix
    space, so the listing gives no hint about how many real files live
    under each directory.
    """

    name = secrets.token_hex(_CONTENT_NAME_BYTES)
    digest = hashlib.blake2b(name.encode("ascii"), digest_size=2).hexdigest()
    rel = f"v/{digest[0:2]}/{digest[2:4]}/{name}.c9r"
    full = vault_dir(data_dir) / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    return full, rel


def vault_thumbnail_path(data_dir: Path, asset_id: int, size: str) -> Path:
    """Encrypted thumbnail path for an asset id and size bucket."""

    if size not in _THUMB_SIZES:
        raise ValueError(f"unknown thumbnail size {size!r}")
    p = vault_dir(data_dir) / "t" / str(asset_id) / f"{size}.c9r"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def vault_keyframe_path(
    data_dir: Path,
    asset_id: int,
    scene: int,
    index: int,
) -> Path:
    """Encrypted keyframe path for an asset id and scene/index."""

    p = vault_dir(data_dir) / "k" / str(asset_id) / f"scene-{scene}-{index}.c9r"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def resolve_content_path(data_dir: Path, vault_rel: str) -> Path:
    """Translate a stored ``encrypted_path`` back into an absolute path."""

    # Defensive: reject any path that tries to escape the vault root.
    root = vault_dir(data_dir).resolve()
    candidate = (root / vault_rel).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("vault path escapes vault root")
    return candidate