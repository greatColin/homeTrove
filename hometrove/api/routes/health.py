from __future__ import annotations

from fastapi import APIRouter, Depends

from hometrove.api.deps import current_principal
from hometrove.auth import Principal

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@router.get("/_debug/principal", include_in_schema=False)
def debug_principal(
    principal: Principal = Depends(current_principal),
) -> dict:
    """Return the resolved principal (debug only, hidden from OpenAPI)."""
    return {
        "id": principal.id,
        "name": principal.name,
        "is_authenticated": principal.is_authenticated,
        "scopes": sorted(principal.scopes),
    }
