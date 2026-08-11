"""homeTrove HTTP route package — exports per-module routers."""

from hometrove.api.routes import (
    albums,
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

__all__ = [
    "albums",
    "assets",
    "facets",
    "folders",
    "health",
    "jobs",
    "persons",
    "places",
    "plugins",
    "search",
    "upload_presets",
    "vault",
]