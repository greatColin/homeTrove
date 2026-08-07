from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment.

    ``media_roots`` is held as a string (``HOMETROVE_MEDIA_ROOTS``) — empty
    means no roots, otherwise paths joined by ``|``. ``media_roots_paths``
    parses it into ``list[Path]``.
    """

    model_config = SettingsConfigDict(env_prefix="HOMETROVE_", case_sensitive=False)

    data_dir: Path = Field(default_factory=lambda: Path("var"))
    database_url: str | None = None

    media_roots: str = ""     # path1|path2  ; empty => none

    upload_dir: Path | None = None
    upload_chunk_size_mb: int = 4
    upload_session_ttl_seconds: int = 24 * 3600

    hash_prefix_bytes: int = 1024 * 1024
    scanner_poll_interval_seconds: int = 30

    worker_concurrency: int = 2
    worker_poll_interval_seconds: float = 0.5

    # M1-11: auth scaffold. v1 ships with a passthrough backend that always
    # returns a single "local" principal; v1.1 will add session/token/oidc
    # backends that flip ``require_auth`` dependencies to gate routes.
    auth_backend: str = "passthrough"
    auth_cookie_name: str = "hometrove_session"
    auth_session_ttl_seconds: int = 7 * 24 * 3600

    # M0-3: media-root read-only verification.
    # ``warn`` (default) prints one WARNING per writable root at startup;
    # ``off`` skips the probe entirely (dev / scratch mounts).
    read_only_check: str = "warn"

    # v1 trash (soft delete): ``deleted_at`` is set on trashing; assets are
    # hidden from default views until restored or purged. Permanent purge
    # is row-only — the on-disk file is never touched because M0 assumes
    # scanned media roots are read-only mounts.
    trash_retention_days: int = 30
    trash_auto_purge: bool = False  # set True to enable worker-side sweep

    log_level: str = "INFO"

    def resolved_data_dir(self) -> Path:
        p = self.data_dir
        if not p.is_absolute():
            p = Path.cwd() / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db_path = self.resolved_data_dir() / "hometrove.db"
        return f"sqlite:///{db_path.as_posix()}"

    def resolved_upload_dir(self) -> Path:
        p = self.upload_dir or (self.resolved_data_dir() / "uploads")
        if not p.is_absolute():
            p = Path.cwd() / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_roots_paths(self) -> list[Path]:
        if not self.media_roots:
            return []
        return [Path(os.fspath(s)).resolve() for s in self.media_roots.split("|") if s]


_cached: Settings | None = None


def get_settings() -> Settings:
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def reset_settings_cache() -> None:
    global _cached
    _cached = None
