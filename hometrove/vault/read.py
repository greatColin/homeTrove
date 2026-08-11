"""Single read entry point for asset bytes.

Both the HTTP layer and the plugin worker route through
:func:`read_asset_bytes` / :func:`read_asset_stream` so that the
encryption decision lives in exactly one place.  The function inspects
the asset's ``encrypted_path`` column:

* ``None`` — resolve the plaintext path through the existing logic
  and read the file as before.
* non-``None`` — if the vault is unlocked, decrypt and return; if it
  is locked, return the matching placeholder bytes.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import AsyncIterator

from hometrove.models import Asset
from hometrove.vault import is_unlocked
from hometrove.vault.paths import (
    placeholders_dir,
    resolve_content_path,
    vault_dir,
)
from hometrove.vault.placeholders import placeholder_for_media_type
from hometrove.vault.state import get_state
from hometrove.vault.stream import decrypt_stream_async, decrypt_to_bytes


def _resolve_plain_path(asset: Asset) -> Path | None:
    """Replicate the legacy ``_asset_path`` logic.

    Two layouts are recognised:
    * ``uploads\\0{absolute_staging_path}`` — user uploads still live
      in the staging directory.
    * ``{media_root}\\0{relative}`` — scanned media under a configured
      media root.
    Returns ``None`` if the path cannot be resolved or escapes the root.
    """

    raw = asset.path or ""
    if "\0" not in raw:
        return None
    kind, _, rest = raw.partition("\0")
    if kind == "uploads":
        p = Path(rest)
        return p if p.is_file() else None
    root = Path(kind)
    try:
        resolved = (root / rest).resolve()
    except OSError:
        return None
    if resolved.is_file() and resolved.is_relative_to(root.resolve()):
        return resolved
    return None


def is_asset_encrypted(asset: Asset) -> bool:
    return bool(asset.encrypted_path)


def read_asset_bytes(asset: Asset) -> tuple[bytes, str]:
    """Return ``(bytes, mime_type)`` for an asset, transparently.

    * Plain asset → bytes from the underlying path.
    * Encrypted asset + vault unlocked → decrypted bytes.
    * Encrypted asset + vault locked → placeholder bytes (200 OK).
    """

    if not is_asset_encrypted(asset):
        path = _resolve_plain_path(asset)
        if path is None:
            return _placeholder_bytes(asset)
        mime = mimetypes.guess_type(path.name)[0] or _mime_from_media_type(asset.media_type)
        return path.read_bytes(), mime

    if not is_unlocked():
        return _placeholder_bytes(asset)

    state = get_state()
    if not state.subkeys:
        return _placeholder_bytes(asset)

    data_dir = Path(state.data_dir) if hasattr(state, "data_dir") else _data_dir()
    enc_path = resolve_content_path(data_dir, asset.encrypted_path)
    if not enc_path.is_file():
        return _placeholder_bytes(asset)
    plaintext = decrypt_to_bytes(
        enc_path,
        key=bytes(state.subkeys.content_enc_key),
        asset_id=asset.id,
    )
    mime = mimetypes.guess_type(asset.filename or "")[0] or _mime_from_media_type(asset.media_type)
    return plaintext, mime


def read_asset_stream(
    asset: Asset,
    *,
    range_header: str | None = None,
) -> tuple[AsyncIterator[bytes], str, int | None]:
    """Return ``(async_iterator, mime_type, total_size)`` for streaming.

    Plain assets delegate to FastAPI's :class:`FileResponse`-style
    handling via the existing endpoints; this function only handles
    encrypted assets.  For unlocked encrypted assets we yield plaintext
    chunks via :func:`decrypt_stream_async`; for locked ones we return
    the placeholder bytes as a single yield.
    """

    if not is_asset_encrypted(asset):
        # Should not normally be called for plain assets; the HTTP layer
        # uses the existing ``FileResponse`` path for those.
        path = _resolve_plain_path(asset)
        if path is None:
            return _placeholder_stream(asset)
        mime = mimetypes.guess_type(path.name)[0] or _mime_from_media_type(asset.media_type)
        size = path.stat().st_size

        async def _plain_iter() -> AsyncIterator[bytes]:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        return _plain_iter(), mime, size

    if not is_unlocked():
        return _placeholder_stream(asset)

    state = get_state()
    if not state.subkeys:
        return _placeholder_stream(asset)

    data_dir = _data_dir()
    enc_path = resolve_content_path(data_dir, asset.encrypted_path)
    if not enc_path.is_file():
        return _placeholder_stream(asset)
    mime = mimetypes.guess_type(asset.filename or "")[0] or _mime_from_media_type(asset.media_type)
    size = enc_path.stat().st_size

    async def _iter() -> AsyncIterator[bytes]:
        async for chunk in decrypt_stream_async(
            enc_path,
            key=bytes(state.subkeys.content_enc_key),
            asset_id=asset.id,
        ):
            if range_header:
                # Naive Range handling: yield everything, let the HTTP
                # layer apply byte slicing.  A future v2.x optimisation
                # will align decrypt chunks with the requested range.
                pass
            yield chunk

    return _iter(), mime, size


def _placeholder_bytes(asset: Asset) -> tuple[bytes, str]:
    from hometrove.vault.placeholders import ensure_placeholders
    data_dir = _data_dir()
    ensure_placeholders(data_dir)
    path, mime = placeholder_for_media_type(data_dir, asset.media_type)
    if not path.is_file():
        return b"", mime
    return path.read_bytes(), mime


def _placeholder_stream(asset: Asset):
    payload, mime = _placeholder_bytes(asset)
    size = len(payload)

    async def _iter() -> AsyncIterator[bytes]:
        yield payload

    return _iter(), mime, size


def _mime_from_media_type(media_type: str | None) -> str:
    if media_type == "image":
        return "image/jpeg"
    if media_type == "video":
        return "video/mp4"
    if media_type == "audio":
        return "audio/mpeg"
    return "application/octet-stream"


def _data_dir() -> Path:
    """Locate the current ``data_dir`` from runtime settings."""

    from hometrove.config import get_settings

    return get_settings().resolved_data_dir()


def ensure_vault_dirs() -> None:
    """Create ``vault/`` and ``placeholders/`` under the data dir."""

    from hometrove.config import get_settings

    data_dir = get_settings().resolved_data_dir()
    vault_dir(data_dir)  # creates the root
    placeholders_dir(data_dir)  # creates the placeholders root