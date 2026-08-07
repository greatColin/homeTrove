"""FastAPI dependencies exposing the request principal to routes.

v1 routes do not need to add ``Depends(current_principal)`` — every
request gets a ``local`` principal by default. The hook exists so v1.1 can
add ``Depends(require_auth)`` to specific routes without a refactor.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from hometrove.auth import Principal
from hometrove.api.middleware import get_principal as _get_principal


def current_principal(
    request: Request,
) -> Principal:
    """Return the principal resolved by ``AuthMiddleware``."""
    return _get_principal(request)


def require_auth(
    principal: Principal = Depends(current_principal),
) -> Principal:
    """Reject the request with 401 when the principal is anonymous.

    The default ``PassthroughAuthBackend`` always returns
    ``is_authenticated=False``, so adding this dependency to a route is
    what flips it to "login required" once a real backend is wired in.
    """

    if not principal.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return principal


def require_scope(scope: str):  # type: ignore[no-untyped-def]
    """Build a dependency that enforces a specific scope on the principal."""

    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.is_authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        if scope not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"scope '{scope}' required",
            )
        return principal

    return _dep
