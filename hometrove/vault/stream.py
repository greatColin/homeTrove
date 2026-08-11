"""Streaming encrypt / decrypt for vault files.

The vault file format is::

    +---------------------------+
    | magic 8B:  "HTV1" + 4 zeros|
    +---------------------------+
    | nonce 12B: AES-GCM nonce  |
    +---------------------------+
    | chunk 0:                  |
    |   len_be64 (8B)           |
    |   ciphertext (n bytes)    |
    |   tag (16 bytes)          |
    +---------------------------+
    | chunk 1: ...              |
    +---------------------------+

Each chunk is ``CHUNK_BYTES`` plaintext bytes (default 64 KiB) and is
authenticated with AES-256-GCM.  The AAD is ``htv1:<asset_id>:<n>``
where ``<n>`` is the chunk index; this binds each chunk to both the
file (asset id) and its position so the same nonce can be reused across
chunks without breaking GCM's authentication guarantees.

The streaming interface yields ``bytes`` chunks so callers (FastAPI's
``StreamingResponse``) can pipe them straight to the client without
ever materialising the plaintext in memory.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import AsyncIterator

from hometrove.vault import crypto
from hometrove.vault.mem import memzero

log = logging.getLogger("hometrove.vault.stream")


#: Default chunk size in bytes.  Matches the spec (64 KiB).
CHUNK_BYTES = 64 * 1024

#: Length of the magic header in bytes.
_HEADER_BYTES = 8

#: Length of the per-chunk length prefix in bytes.
_LEN_BYTES = 8

#: Sentinel returned by the write helpers once the file is fully
#: committed; useful for test assertions.
WRITE_OK = "ok"


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------


def _be64(n: int) -> bytes:
    return n.to_bytes(_LEN_BYTES, "big", signed=False)


def _from_be64(b: bytes) -> int:
    return int.from_bytes(b, "big", signed=False)


def _read_header(f) -> bytes:
    """Read and validate the magic prefix only (8 bytes).

    The 12-byte nonce that follows is left for ``_read_nonce``.
    """

    head = f.read(_HEADER_BYTES)
    if len(head) < _HEADER_BYTES:
        raise ValueError("vault file too short for header")
    if head[:4] != crypto.MAGIC_HEADER[:4]:
        raise ValueError("not a homeTrove vault file (magic mismatch)")
    return head


# ---------------------------------------------------------------------------
# Sync streaming encrypt
# ---------------------------------------------------------------------------


def encrypt_file(
    src_path: Path,
    dst_path: Path,
    *,
    key: bytes,
    asset_id: int,
    chunk_bytes: int = CHUNK_BYTES,
) -> bytes:
    """Stream-encrypt ``src_path`` into ``dst_path``.

    Returns the 12-byte nonce so the caller can persist it in
    ``assets.encrypted_nonce``.  The destination file format starts
    with the magic header, then the nonce, then the chunk framing.
    """

    nonce = crypto.generate_nonce()
    tmp_path = dst_path.with_suffix(dst_path.suffix + ".partial")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(src_path, "rb") as src, open(tmp_path, "wb") as dst:
        dst.write(crypto.MAGIC_HEADER)
        dst.write(nonce)
        chunk_no = 0
        while True:
            plaintext = src.read(chunk_bytes)
            if not plaintext:
                break
            aad = crypto.make_aad(asset_id, chunk_no)
            ct_with_tag = crypto.encrypt_chunk(key, nonce, plaintext, aad)
            dst.write(_be64(len(ct_with_tag)))
            dst.write(ct_with_tag)
            chunk_no += 1
            memzero(plaintext)
    os.replace(tmp_path, dst_path)
    return nonce


def encrypt_bytes(
    plaintext: bytes,
    dst_path: Path,
    *,
    key: bytes,
    asset_id: int,
    chunk_bytes: int = CHUNK_BYTES,
) -> bytes:
    """Encrypt an in-memory buffer to ``dst_path``.

    Used for thumbnails / keyframes that already live in memory after
    Pillow / ffmpeg decode them.
    """

    nonce = crypto.generate_nonce()
    tmp_path = dst_path.with_suffix(dst_path.suffix + ".partial")
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_path, "wb") as dst:
        dst.write(crypto.MAGIC_HEADER)
        dst.write(nonce)
        chunk_no = 0
        for offset in range(0, len(plaintext), chunk_bytes):
            chunk = plaintext[offset : offset + chunk_bytes]
            aad = crypto.make_aad(asset_id, chunk_no)
            ct_with_tag = crypto.encrypt_chunk(key, nonce, chunk, aad)
            dst.write(_be64(len(ct_with_tag)))
            dst.write(ct_with_tag)
            chunk_no += 1
    os.replace(tmp_path, dst_path)
    return nonce


# ---------------------------------------------------------------------------
# Sync streaming decrypt
# ---------------------------------------------------------------------------


def decrypt_to_bytes(
    src_path: Path,
    *,
    key: bytes,
    asset_id: int,
) -> bytes:
    """Decrypt an entire vault file into memory.

    Use only for small payloads (thumbnails, keyframes).  Larger files
    should go through :func:`decrypt_stream`.
    """

    out = bytearray()
    with open(src_path, "rb") as f:
        _read_header(f)  # validates magic; nonce inside header is reused
        nonce = _read_nonce(f)
        chunk_no = 0
        while True:
            len_buf = f.read(_LEN_BYTES)
            if not len_buf:
                break
            if len(len_buf) < _LEN_BYTES:
                raise ValueError("vault file truncated in length prefix")
            ct_len = _from_be64(len_buf)
            ct = f.read(ct_len)
            if len(ct) < ct_len:
                raise ValueError("vault file truncated in ciphertext")
            aad = crypto.make_aad(asset_id, chunk_no)
            pt = crypto.decrypt_chunk(key, nonce, ct, aad)
            out.extend(pt)
            chunk_no += 1
    return bytes(out)


def _read_nonce(f) -> bytes:
    nonce = f.read(crypto.NONCE_BYTES)
    if len(nonce) != crypto.NONCE_BYTES:
        raise ValueError("vault file truncated in nonce")
    return nonce


def decrypt_stream(
    src_path: Path,
    *,
    key: bytes,
    asset_id: int,
    chunk_bytes: int = CHUNK_BYTES,
) -> "decrypt_iter":
    """Return an iterator that yields plaintext chunks of ``src_path``.

    Each ``next()`` returns up to ``chunk_bytes`` bytes of plaintext;
    the underlying ``bytes`` object is wiped as soon as the iterator
    advances so a slow consumer does not leave large plaintext buffers
    pinned in memory.
    """

    return decrypt_iter(src_path, key=key, asset_id=asset_id, chunk_bytes=chunk_bytes)


class decrypt_iter:
    """Iterator implementation returned by :func:`decrypt_stream`."""

    def __init__(self, src_path: Path, *, key: bytes, asset_id: int, chunk_bytes: int) -> None:
        self._fp = open(src_path, "rb")
        try:
            _read_header(self._fp)
            self._nonce = _read_nonce(self._fp)
        except Exception:
            self._fp.close()
            raise
        self._key = key
        self._asset_id = asset_id
        self._chunk_bytes = chunk_bytes
        self._chunk_no = 0

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        len_buf = self._fp.read(_LEN_BYTES)
        if not len_buf:
            self.close()
            raise StopIteration
        if len(len_buf) < _LEN_BYTES:
            self.close()
            raise ValueError("vault file truncated in length prefix")
        ct_len = _from_be64(len_buf)
        ct = self._fp.read(ct_len)
        if len(ct) < ct_len:
            self.close()
            raise ValueError("vault file truncated in ciphertext")
        aad = crypto.make_aad(self._asset_id, self._chunk_no)
        pt = crypto.decrypt_chunk(self._key, self._nonce, ct, aad)
        self._chunk_no += 1
        return pt

    def close(self) -> None:
        if self._fp and not self._fp.closed:
            self._fp.close()


# ---------------------------------------------------------------------------
# Async streaming decrypt (for FastAPI StreamingResponse)
# ---------------------------------------------------------------------------


async def decrypt_stream_async(
    src_path: Path,
    *,
    key: bytes,
    asset_id: int,
    chunk_bytes: int = CHUNK_BYTES,
) -> AsyncIterator[bytes]:
    """Yield plaintext chunks asynchronously."""

    it = decrypt_iter(src_path, key=key, asset_id=asset_id, chunk_bytes=chunk_bytes)
    try:
        loop = asyncio.get_running_loop()
        while True:
            chunk = await loop.run_in_executor(None, next, it, None)
            if chunk is None:
                break
            yield chunk
    finally:
        it.close()


# ---------------------------------------------------------------------------
# Secure delete (shred)
# ---------------------------------------------------------------------------


def shred_file(path: Path) -> None:
    """Overwrite ``path`` with zeros before unlinking.

    Best-effort: on filesystems with copy-on-write (btrfs, APFS, ZFS)
    the underlying blocks may already be released; we still zero the
    file's bytes to make casual recovery harder.
    """

    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= 0:
        try:
            path.unlink()
        except OSError:
            return
        return
    try:
        with open(path, "r+b") as f:
            chunk = bytearray(min(size, CHUNK_BYTES))
            remaining = size
            while remaining > 0:
                n = min(len(chunk), remaining)
                f.write(chunk[:n])
                remaining -= n
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except OSError as exc:
        log.warning("shred write failed for %s: %s", path, exc)
    try:
        path.unlink()
    except OSError as exc:
        log.warning("shred unlink failed for %s: %s", path, exc)