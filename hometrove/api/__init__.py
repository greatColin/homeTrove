"""FastAPI app factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from hometrove.auth import Principal, TokenAuthBackend, build_auth_backend
from hometrove.config import get_settings
from hometrove.db import engine, session_scope
import hometrove.plugins.builtin  # noqa: F401  registers basic.info + mock plugins
from hometrove.plugins.registry import REGISTRY
from hometrove.uploads import UploadManager, build_router as build_uploads_router
from hometrove.api.deps import current_principal
from hometrove.api.middleware import AuthMiddleware
from hometrove.api.routes import (
    albums,
    app_settings,
    assets,
    facets,
    folders,
    health,
    jobs,
    persons,
    places,
    plugins,
    search,
    upload_presets,
    vault,
)
from hometrove.models import PluginConfig
from hometrove.vault import placeholders as _vault_placeholders
from hometrove.vault.paths import vault_dir
from hometrove.vault.state import VaultStatus, get_state, set_disabled


def _web_dist_dir() -> Path | None:
    """Locate the built frontend (``web/dist``), if present."""
    for base in (Path(__file__).parent.parent.parent, Path.cwd()):
        d = base / "web" / "dist"
        if d.is_dir() and (d / "index.html").is_file():
            return d
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    settings = get_settings()
    settings.resolved_data_dir()
    # ``engine()`` bootstraps the schema (and sqlite-vec tables) exactly once
    # under a lock — the worker thread and the API server share that path, so
    # no separate create_all() here (it would race the worker's).
    engine()

    # M0-3: warn when a configured media root is writable. The probe runs
    # once per process at startup; opt out via HOMETROVE_READ_ONLY_CHECK=off.
    from hometrove.readonly import run_for_settings

    run_for_settings(settings, logger=logging.getLogger("hometrove.readonly"))

    with session_scope() as s:
        rows = {r.plugin_id: r for r in s.query(PluginConfig).all()}
        for p in REGISTRY.list():
            row = rows.get(p.id)
            if row is None:
                s.add(PluginConfig(plugin_id=p.id, enabled=1))
        s.commit()

        # Start enabled plugins so heavy resources (models, pools) are loaded at
        # boot time rather than on first asset. Stub-category plugins are
        # registered for completeness but never auto-started — they're shown
        # in the plugin list with the "未实现" badge and disabled at runtime.
        enabled_ids = {
            p.id for p in REGISTRY.list()
            if getattr(p, "category", None) != "stub"
            and (rows.get(p.id) is None or bool(rows.get(p.id).enabled))
        }

    from hometrove.plugins.lifecycle import start_all_enabled

    start_all_enabled(enabled_ids)

    # v2 content encryption: bootstrap vault state from settings + DB.
    settings = get_settings()
    if not settings.vault_enabled:
        set_disabled()
    else:
        # Hydrate the in-memory cached KDF params + wrapped key so the
        # unlock endpoint can re-derive the master key without a second
        # DB round-trip.
        from hometrove.vault import crypto as _vault_crypto
        from hometrove.models import VaultState as _VaultStateRow

        state = get_state()
        if state.status == VaultStatus.DISABLED:
            with session_scope() as s:
                row = s.get(_VaultStateRow, 1)
            if row is None:
                # Fresh deployment: leave state as INITIALIZED so the
                # client redirects to /vault/setup.
                state.status = VaultStatus.INITIALIZED
            else:
                state.kdf_salt = row.kdf_salt
                state.kdf_params = _vault_crypto.kdf_params_from_json(row.kdf_params_json)
                state.wrapped_master_key = row.wrapped_master_key
                state.status = VaultStatus.LOCKED

        # Ensure the placeholder assets exist so the locked-asset UX is
        # never a 404 even on a brand new install.
        _vault_placeholders.ensure_placeholders(settings.resolved_data_dir())
        # Create the vault root early so the worker can write into it
        # without racing the first upload.
        vault_dir(settings.resolved_data_dir())
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="HomeTrove API",
        version="0.1.0",
        description="M0 skeleton — browse, scan, basic plugin, chunked upload.",
        lifespan=lifespan,
    )

    settings = get_settings()
    app.state.upload_manager = UploadManager(
        base_dir=settings.resolved_upload_dir(),
        default_chunk_mb=settings.upload_chunk_size_mb,
        ttl_seconds=settings.upload_session_ttl_seconds,
    )

    # M1-11 auth scaffold: register the middleware before any route so every
    # request is resolved to a ``Principal`` (default = passthrough local).
    # ``app.state.auth_backend`` is left open for tests to monkeypatch a
    # counting/stub backend without rebuilding the app.
    # Agent CLI: when ``cli_api_key`` is set, force token auth so the CLI key
    # is honored even if the default ``auth_backend`` is ``passthrough``.
    if settings.cli_api_key and settings.auth_backend == "passthrough":
        app.state.auth_backend = TokenAuthBackend(api_key=settings.cli_api_key)
    else:
        app.state.auth_backend = build_auth_backend(settings)
    app.add_middleware(AuthMiddleware, backend=app.state.auth_backend)

    # Order matters: everything below must be registered before the SPA
    # catch-all ``/{full_path:path}`` route so API paths win.
    app.include_router(health.router)
    app.include_router(build_uploads_router(app.state.upload_manager))
    app.include_router(assets.router)
    app.include_router(facets.router)
    app.include_router(persons.router)
    app.include_router(folders.router)
    app.include_router(jobs.router)
    app.include_router(plugins.router)
    app.include_router(search.router)
    app.include_router(albums.router)
    app.include_router(places.router)
    app.include_router(upload_presets.router)
    app.include_router(app_settings.router)
    app.include_router(vault.router)
    # Public share endpoints are registered last but before the SPA fallback.
    # They live inside assets.router to keep the prefix surface consistent.

    dist = _web_dist_dir()
    if dist is not None:
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="web-assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_fallback(full_path: str) -> Response:
            # Never hijack API routes (they are registered above and matched first).
            if full_path.startswith("api/"):
                return Response(status_code=404)
            candidate = dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    return app
