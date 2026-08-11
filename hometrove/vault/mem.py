"""Memory-safe helpers: zero a buffer in place.

This is a thin wrapper around ``ctypes.memset`` so we get a real libc
``memset(buf, 0, n)`` call instead of relying on Python's ``bytearray``
``__setitem__`` (which is a few hundred nanoseconds per byte and
silently no-ops on read-only views).  ``pynacl`` >= 1.6 does not expose
``sodium_memzero`` / ``sodium_mlock`` from the public ``bindings``
namespace, hence the local replacement.
"""
from __future__ import annotations

import ctypes
from ctypes import c_void_p, c_int, c_size_t

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
_memset = _libc.memset
_memset.restype = c_void_p
_memset.argtypes = [c_void_p, c_int, c_size_t]


def memzero(buf: bytearray | bytes) -> None:
    """Overwrite ``buf`` with zeros.

    Works on ``bytearray`` (mutable).  On ``bytes`` the call silently
    no-ops — ``bytes`` is immutable, so the caller is expected to use
    ``bytearray`` when holding sensitive material.
    """

    if not isinstance(buf, bytearray) or not buf:
        return
    addr = ctypes.addressof((ctypes.c_char * len(buf)).from_buffer(buf))
    _memset(addr, 0, len(buf))


def is_zero(buf: bytes | bytearray) -> bool:
    return all(b == 0 for b in buf)