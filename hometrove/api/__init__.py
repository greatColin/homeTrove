"""FastAPI app factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from hometrove.config import get_settings
from hometrove.db import engine, session_scope
import hometrove.plugins.builtin  # noqa: F401  registers basic.info + mock plugins
from hometrove.plugins.registry import REGISTRY
from hometrove.uploads import UploadManager, build_router as build_uploads_router
from hometrove.api.routes import assets, facets, folders, jobs, health, persons, plugins, search
from hometrove.models import PluginConfig


def _web_dist_dir() -> Path | None:
    """Locate the built frontend (``web/dist``), if present."""
    for base in (Path(__file__).parent.parent.parent, Path.cwd()):
        d = base / "web" / "dist"
        if d.is_dir() and (d / "index.html").is_file():
            return d
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.resolved_data_dir()
    # ``engine()`` bootstraps the schema (and sqlite-vec tables) exactly once
    # under a lock — the worker thread and the API server share that path, so
    # no separate create_all() here (it would race the worker's).
    engine()

    with session_scope() as s:
        for p in REGISTRY.list():
            row = s.get(PluginConfig, p.id)
            if row is None:
                s.add(PluginConfig(plugin_id=p.id, enabled=1))
        s.commit()
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
