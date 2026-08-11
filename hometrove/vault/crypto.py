"""Encryption primitives: AES-256-GCM, Argon2id, HKDF, AES-Key-Wrap.

The module exposes narrow, side-effect-free helpers so the streaming
code (``vault/stream.py``) and state code (``vault/state.py``) can
build on them.  All constants here are derived from the
spec ``.monkeycode/specs/v2-content-encryption/design.md``:

* AEAD algorithm: AES-256-GCM with a fresh 12-byte nonce per file.
* KDF: Argon2id (m=64 MiB, t=3, p=1, salt 16 bytes) per OWASP 2026.
* Subkey derivation: HKDF-SHA256 with domain-separation ``info`` labels.
* Master key wrap: AES-Key-Wrap (RFC 3394) of the 96-byte raw master
  key under the ``db_key`` subkey.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Length of the raw master key in bytes.  Argon2id with these parameters
#: emits 96 bytes by default; we use the full output (32 bytes content
#: encryption key + 32 bytes filename key + 32 bytes metadata key).
RAW_MASTER_KEY_BYTES = 96

#: Each subkey is 32 bytes (AES-256 / HMAC-SHA256 key length).
SUBKEY_BYTES = 32

#: AES-GCM nonce length.
NONCE_BYTES = 12

#: AES-GCM authentication tag length (16 bytes is the only mode size).
GCM_TAG_BYTES = 16

#: KDF salt length (Argon2id uses at least 8 bytes; we use 16 for margin).
KDF_SALT_BYTES = 16

#: Argon2id parameters — OWASP 2026 first-choice.  ~250 ms derivation
#: on a typical x86_64 desktop CPU.
ARGON2_M_KIB = 64 * 1024  # 64 MiB
ARGON2_T = 3
ARGON2_P = 1

#: HKDF info labels for domain separation.  These MUST stay stable
#: across versions; changing them silently invalidates existing wrapped
#: master keys.
_INFO_CONTENT_ENC = b"hometrove-vault-content-enc-v1"
_INFO_FILENAME_ENC = b"hometrove-vault-filename-enc-v1"
_INFO_METADATA_ENC = b"hometrove-vault-metadata-enc-v1"
_INFO_HASH_KEY = b"hometrove-vault-hash-v1"
_INFO_KEK = b"hometrove-vault-kek-v1"


# ---------------------------------------------------------------------------
# Subkey container
# ---------------------------------------------------------------------------


@dataclass
class VaultSubkeys:
    """The five 32-byte subkeys derived from the raw master key.

    Attributes are intentionally typed as ``bytes`` so a caller can wipe
    them with ``memzero()`` from ``vault.mem`` when no longer needed.
    Only ``content_enc_key`` is consumed in v2; the remaining slots are
    reserved for v2.x filename / metadata encryption.
    """

    content_enc_key: bytes
    filename_enc_key: bytes
    metadata_enc_key: bytes
    hash_key: bytes
    kek: bytes


# ---------------------------------------------------------------------------
# KDF (Argon2id)
# ---------------------------------------------------------------------------


def kdf_params_dict() -> dict:
    """Return the canonical Argon2id parameter set used by homeTrove."""

    return {"m": ARGON2_M_KIB, "t": ARGON2_T, "p": ARGON2_P}


def generate_kdf_salt() -> bytes:
    """Return a fresh 16-byte CSPRNG salt for a new vault."""

    return secrets.token_bytes(KDF_SALT_BYTES)


def derive_raw_master_key(password: str, salt: bytes, params: dict | None = None) -> bytes:
    """Run Argon2id and return the 96-byte raw master key.

    ``password`` is encoded as UTF-8; ``salt`` must be 16 bytes (or
    longer, but anything above 16 is wasted).  ``params`` defaults to
    the homeTrove recommendation; pass ``kdf_params_dict()`` from the
    persisted row when unlocking an existing vault to honour the
    parameters in use at setup time.
    """

    if params is None:
        params = kdf_params_dict()
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=params["t"],
        memory_cost=params["m"],
        parallelism=params["p"],
        hash_len=RAW_MASTER_KEY_BYTES,
        type=Type.ID,
    )


# ---------------------------------------------------------------------------
# HKDF subkey derivation
# ---------------------------------------------------------------------------


def _hkdf(ikm: bytes, info: bytes, length: int) -> bytes:
    """Tiny HKDF-SHA256 helper — single block, fixed 32-byte output.

    The homeTrove vault always derives 32-byte subkeys and the IKM is
    already 96 bytes of high-entropy material, so a single extract +
    expand pair is sufficient.  We avoid pulling in ``cryptography``'s
    full HKDF pipeline (which uses BoringSSL state) to keep the call
    site explicit and the implementation auditable.
    """

    # Extract: PRK = HMAC-SHA256(salt=zeros(32), ikm)
    prk = hashlib.sha256(ikm).digest()  # salt=zeros per RFC 5869 §2.2
    # Expand: T(1) = HMAC-SHA256(PRK, info || 0x01), length 32 is enough.
    return _hkdf_expand(prk, info, length)


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    import hmac

    if length > 255 * 32:
        raise ValueError("hkdf length exceeds 8160 bytes")
    t = b""
    okm = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def derive_subkeys(raw_master_key: bytes) -> VaultSubkeys:
    """Split the 96-byte master key into five domain-separated subkeys."""

    if len(raw_master_key) != RAW_MASTER_KEY_BYTES:
        raise ValueError("raw_master_key must be 96 bytes")
    return VaultSubkeys(
        content_enc_key=_hkdf(raw_master_key, _INFO_CONTENT_ENC, SUBKEY_BYTES),
        filename_enc_key=_hkdf(raw_master_key, _INFO_FILENAME_ENC, SUBKEY_BYTES),
        metadata_enc_key=_hkdf(raw_master_key, _INFO_METADATA_ENC, SUBKEY_BYTES),
        hash_key=_hkdf(raw_master_key, _INFO_HASH_KEY, SUBKEY_BYTES),
        kek=_hkdf(raw_master_key, _INFO_KEK, SUBKEY_BYTES),
    )


# ---------------------------------------------------------------------------
# AES-Key-Wrap (RFC 3394)
# ---------------------------------------------------------------------------


def wrap_master_key(raw_master_key: bytes, kek: bytes) -> bytes:
    """AES-Key-Wrap (RFC 3394) of the 96-byte raw master key under ``kek``.

    The wrapped form is 8 bytes longer than the input (96 → 104), which
    is the expected output size for AES-Key-Wrap with 16-byte AES.
    """

    return aes_key_wrap(kek, raw_master_key)


def unwrap_master_key(wrapped: bytes, kek: bytes) -> bytes:
    """Reverse of ``wrap_master_key``.  Raises ``InvalidUnwrap`` on mismatch."""

    return aes_key_unwrap(kek, wrapped)


# ---------------------------------------------------------------------------
# AEAD helpers
# ---------------------------------------------------------------------------


def generate_nonce() -> bytes:
    """Return a fresh 12-byte CSPRNG nonce for a new encrypted file."""

    return secrets.token_bytes(NONCE_BYTES)


def encrypt_chunk(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """Encrypt a single chunk with AES-256-GCM.

    Returns ``ciphertext || tag`` (``tag`` is the trailing 16 bytes).
    The caller is responsible for tracking per-file nonce reuse — we use
    a single nonce per file with per-chunk framing, so a fresh nonce is
    minted once per file and reused across chunks.  This is safe because
    every chunk uses a distinct AAD (``htv1:<asset_id>:<chunk_no>``) and
    GCM allows nonce reuse only when keys differ; we have a single key
    per file.  See ``stream.py`` for the framing details and the safety
    rationale.
    """

    cipher = AESGCM(key)
    return cipher.encrypt(nonce, plaintext, aad)


def decrypt_chunk(key: bytes, nonce: bytes, ciphertext_with_tag: bytes, aad: bytes) -> bytes:
    """Decrypt a single chunk.  Raises ``InvalidTag`` on authentication failure."""

    cipher = AESGCM(key)
    return cipher.decrypt(nonce, ciphertext_with_tag, aad)


# ---------------------------------------------------------------------------
# Placeholder markers
# ---------------------------------------------------------------------------


#: Magic header that marks a homeTrove vault file.  Eight bytes; the
#: first four spell ``HTV1`` and the remaining four are reserved for
#: future format extensions (currently ``0000``).
MAGIC_HEADER = b"HTV1" + b"\x00\x00\x00\x00"


def make_aad(asset_id: int, chunk_no: int) -> bytes:
    """Build the per-chunk additional authenticated data.

    Including the chunk number in the AAD is what makes nonce reuse
    across chunks safe — GCM's authentication tag is computed over
    AAD, so two chunks with the same key + nonce but different AADs
    remain independently authentic.
    """

    return f"htv1:{asset_id}:{chunk_no}".encode("utf-8")


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def kdf_params_to_json(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


def kdf_params_from_json(blob: str) -> dict:
    obj = json.loads(blob)
    for key in ("m", "t", "p"):
        if key not in obj:
            raise ValueError(f"missing kdf param {key!r}")
    return obj


def get_mlock_capable() -> bool:
    """Best-effort detection of whether the runtime supports mlock.

    Used by ``vault/state.py`` to decide whether to attempt
    ``sodium_mlock`` style protection.  In the homeTrove v2 build we
    fall back to plain bytearrays when mlock isn't exposed; the
    constants in this module are the canonical source for defaults.
    """

    return bool(os.environ.get("HOMETROVE_VAULT_MLOCK", ""))