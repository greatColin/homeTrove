"""ASGI middleware that wires an ``AuthBackend`` into every request.

The middleware is intentionally narrow: it asks the backend for a
``Principal`` and stores it on ``request.state.principal``. Anything beyond
that (response codes, scope checks) is the backend's job; v1's
``PassthroughAuthBackend`` always returns a local principal, so the API
stays open.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from hometrove.auth import AuthBackend, Principal

_PRINCIPAL_KEY = "principal"


def get_principal(request: Request) -> Principal:
    """Read the principal placed on the request by ``AuthMiddleware``."""
    return getattr(request.state, _PRINCIPAL_KEY, Principal.local())


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve a principal per request via the configured backend.

    The backend is resolved from ``app.state.auth_backend`` if present so
    tests can monkeypatch a counting/stub backend without rebuilding the
    app. The middleware never raises: a backend failure (which shouldn't
    happen with the default passthrough) degrades to a local principal so
    the request still reaches a route handler that can decide what to do.
    """

    def __init__(self, app, backend: AuthBackend | None = None) -> None:
        super().__init__(app)
        self._default_backend = backend

    def _resolve_backend(self, request: Request) -> AuthBackend | None:
        backend = getattr(request.app.state, "auth_backend", None)
        if backend is not None:
            return backend  # type: ignore[no-any-return]
        return self._default_backend

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        backend = self._resolve_backend(request)
        principal: Principal = Principal.local()
        if backend is not None:
            try:
                principal = backend.authenticate(request)
            except Exception:  # noqa: BLE001 — degrade open, never crash middleware
                principal = Principal.local()
        request.state.principal = principal
        response: Response = await call_next(request)
        return response
