"""M0-3: media-root read-only verification.

HomeTrove's privacy stance is that original media directories are
mounted read-only — the application never modifies a user's source
files. This module is the lightweight, opt-out safety net that flags
when a configured root is **writable** so the operator can decide
whether that is intentional.

The check is intentionally a *probe*, not a hardening step:

* it writes a single 1-byte sentinel file to a random name inside each
  root, then immediately removes it;
* failures are surfaced as ``WARNING`` logs grouped per-root with the
  full path, so the operator sees exactly which mount bypassed the
  read-only assumption;
* a successful probe does **not** persist any file or directory
  permission change.

The probe runs at three lifecycle points: the API server / worker's
``lifespan`` startup, the ``hometrove serve`` combined process, and the
``hometrove scan`` CLI. ``HOMETROVE_READ_ONLY_CHECK=off`` opts the
operator out entirely (useful for dev / test runners that intentionally
point at a writable scratch directory).

**Note on root**: when the process runs as ``uid=0`` the kernel bypasses
the standard DAC permission checks, so ``os.access`` and ``O_CREAT``
succeed even on a read-only mount. The probe detects this case (by
asking for mode ``0o600`` and checking whether the *effective* mode on
disk respects that), and reports ``writable=False`` with a ``detail``
explaining that DAC is not enforceable here. Operators who run the
server as root should rely on mount flags (``mount -o ro``) instead.
"""

from __future__ import annotations

import logging
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RootCheck:
    """Result of probing one media root for writability."""

    root: Path
    writable: bool
    detail: str = ""

    @property
    def status(self) -> str:
        return "WRITABLE" if self.writable else "read-only"


def _running_as_root() -> bool:
    """Best-effort: ``getuid`` works on POSIX, fall back to ``False``."""
    getuid = getattr(os, "getuid", None)
    return bool(getuid and getuid() == 0)


def _probe_writable(root: Path) -> RootCheck:
    """Return ``RootCheck`` describing whether ``root`` accepts writes."""
    if not root.exists():
        return RootCheck(root=root, writable=False, detail="path missing")
    if not root.is_dir():
        return RootCheck(root=root, writable=False, detail="not a directory")
    if not os.access(str(root), os.W_OK):
        return RootCheck(root=root, writable=False, detail="os.access(W_OK)=false")

    sentinel = f".hometrove-readonly-probe-{uuid.uuid4().hex}.tmp"
    target = root / sentinel
    try:
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        return RootCheck(root=root, writable=True, detail=f"open failed: {exc}")
    try:
        try:
            os.write(fd, b"\x00")
        finally:
            os.close(fd)
    except OSError as exc:
        try:
            target.unlink()
        except OSError:
            pass
        return RootCheck(root=root, writable=True, detail=f"write failed: {exc}")

    # On a uid=0 process DAC is bypassed: the file gets created even on a
    # read-only mount. Asking for ``0o600`` and observing what the kernel
    # actually persists (chmod on a read-only fs returns EPERM and the
    # mode stays default 0o644) is the reliable signal.
    if _running_as_root():
        try:
            os.chmod(target, 0o600)
            actual_mode = stat.S_IMODE(os.stat(target).st_mode)
            target.unlink()
            if actual_mode & 0o077:
                return RootCheck(
                    root=root,
                    writable=False,
                    detail="DAC bypassed (running as root); remount -o ro to enforce",
                )
            return RootCheck(root=root, writable=True)
        except OSError as exc:
            try:
                target.unlink()
            except OSError:
                pass
            return RootCheck(
                root=root,
                writable=False,
                detail=f"DAC bypassed (running as root); chmod/unlink failed: {exc}",
            )

    try:
        target.unlink()
    except OSError as exc:
        return RootCheck(root=root, writable=True, detail=f"unlink failed: {exc}")
    return RootCheck(root=root, writable=True)


def check_roots(
    roots: Iterable[Path],
    *,
    enabled: bool = True,
) -> list[RootCheck]:
    """Probe each root. Returns an empty list when ``enabled`` is False.

    The function never raises — every probe error is captured into the
    returned ``RootCheck`` so callers can log a stable per-root summary.
    """
    if not enabled:
        return []
    out: list[RootCheck] = []
    for root in roots:
        try:
            out.append(_probe_writable(Path(root)))
        except Exception as exc:  # noqa: BLE001  — defensive: never crash startup
            out.append(RootCheck(root=Path(root), writable=False, detail=f"{type(exc).__name__}: {exc}"))
    return out


def log_report(results: Iterable[RootCheck], *, logger: Optional[logging.Logger] = None) -> None:
    """Emit one ``WARNING`` per writable root, one ``INFO`` per read-only.

    Keeps a stable human-readable line so operators can grep the boot
    log. When ``results`` is empty (operator opted out) nothing is
    emitted — opt-out should be silent.
    """
    logger = logger or log
    writable = [r for r in results if r.writable]
    readonly = [r for r in results if not r.writable]
    if writable:
        for r in writable:
            extra = f" ({r.detail})" if r.detail else ""
            logger.warning(
                "media root is WRITABLE — HomeTrove can modify originals: %s%s",
                r.root,
                extra,
            )
        logger.warning(
            "set HOMETROVE_READ_ONLY_CHECK=off to silence, or remount the root read-only (mount -o ro)."
        )
    if readonly:
        for r in readonly:
            logger.info("media root is read-only: %s", r.root)


def run_for_settings(
    settings: object | None = None,
    *,
    logger: Optional[logging.Logger] = None,
) -> list[RootCheck]:
    """Convenience entry point used by lifespan / CLI / tests.

    Reads ``media_roots_paths`` and ``read_only_check`` off a duck-typed
    settings object so this module does not import the real ``Settings``
    (keeps tests fast and the dependency graph flat).
    """
    roots: Iterable[Path] = getattr(settings, "media_roots_paths", []) or []
    enabled = str(getattr(settings, "read_only_check", "warn")).lower() != "off"
    results = check_roots(roots, enabled=enabled)
    if results:
        log_report(results, logger=logger)
    return results


__all__ = [
    "RootCheck",
    "check_roots",
    "log_report",
    "run_for_settings",
]