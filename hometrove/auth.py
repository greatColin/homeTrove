"""Authentication contract.

M1-11: the v1 release ships a single-user deployment, so the auth layer is
intentionally a thin scaffold:

- ``Principal`` describes the caller in a form v1.1 can fill in (per-user
  library, shared albums, etc.) without rewriting call sites.
- ``AuthBackend`` is a ``Protocol`` returning a ``Principal`` per request.
  Implementations stay swappable (passthrough, session, token, OIDC).
- ``PassthroughAuthBackend`` is the v1 default: every request resolves to a
  single ``"local"`` principal and the API stays open.
- ``build_auth_backend(settings)`` selects the backend by name so v1.1 can
  flip the switch via env without touching application code.

The middleware/dependency wiring lives in ``hometrove.api.middleware`` and
``hometrove.api.deps``; this module knows nothing about FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class Principal:
    """Identity of the caller of an API request.

    v1 uses a single principal (``id="local"``) so the rest of the code can
    already pass it through ``Depends(current_principal)`` and persist
    ``principal_id`` when a future model needs it. ``scopes`` is reserved
    for v1.1's role-based checks (``admin`` / ``share`` / etc.).
    """

    id: str
    name: str = "local"
    is_authenticated: bool = False
    scopes: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def local(cls) -> "Principal":
        return cls(id="local", name="local", is_authenticated=False)


@runtime_checkable
class AuthBackend(Protocol):
    """Resolve a request to a ``Principal``.

    The protocol intentionally takes a generic ``request`` so this module
    stays free of FastAPI types. Implementations may inspect headers,
    cookies, or the network peer as needed.
    """

    def authenticate(self, request: object) -> Principal:
        ...


class PassthroughAuthBackend:
    """Default v1 backend: always returns the local principal.

    No token, no cookie, no header. The middleware still runs (so the
    request state is populated and route-level ``Depends`` are exercised),
    but no request is ever rejected.
    """

    def __init__(self, principal: Principal | None = None) -> None:
        self._principal = principal or Principal.local()

    def authenticate(self, request: object) -> Principal:  # noqa: ARG002
        return self._principal


class TokenAuthBackend:
    """API-key backend for the agent CLI.

    Expects ``Authorization: Bearer <token>`` and compares it against the
    configured ``cli_api_key``. The token is compared in constant time to
    mitigate timing attacks.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def authenticate(self, request: object) -> Principal:
        import hmac

        from starlette.requests import Request

        if not isinstance(request, Request):
            return Principal.local()
        if self._api_key is None:
            return Principal.local()
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return Principal.local()
        supplied = header[7:].strip()
        if len(supplied) != len(self._api_key):
            return Principal.local()
        if hmac.compare_digest(supplied, self._api_key):
            return Principal(
                id="cli",
                name="agent-cli",
                is_authenticated=True,
                scopes=frozenset({"cli"}),
            )
        return Principal.local()


def build_auth_backend(
    settings: object | None = None,
    *,
    overrides: Mapping[str, AuthBackend] | None = None,
) -> AuthBackend:
    """Pick a backend by configured name.

    ``settings`` is duck-typed: only ``auth_backend`` is read so the caller
    can pass either a real ``hometrove.config.Settings`` or any test double.
    Unknown names fall back to ``passthrough`` so a typo can't take the API
    down.

    When ``cli_api_key`` is configured, the default backend becomes token
    auth so that the agent CLI can authenticate; ``auth_backend`` can still
    be set to ``passthrough`` to force open access.
    """

    name = getattr(settings, "auth_backend", "passthrough") or "passthrough"
    name = str(name).strip().lower()
    api_key = getattr(settings, "cli_api_key", None)
    registry: Mapping[str, AuthBackend] = overrides or _BACKENDS
    backend = registry.get(name)
    # Token auth needs the configured key; never use the empty fallback in
    # ``_BACKENDS`` when a real key is available.
    if name == "token" and api_key:
        return TokenAuthBackend(api_key=api_key)
    # Auto-enable token auth when an agent CLI key is present and the operator
    # did not explicitly request passthrough.
    if api_key and name != "passthrough":
        return TokenAuthBackend(api_key=api_key)
    if backend is not None:
        return backend
    if api_key:
        return TokenAuthBackend(api_key=api_key)
    return PassthroughAuthBackend()


def available_backends() -> Iterable[str]:
    """Names accepted by ``build_auth_backend`` (for diagnostics / docs)."""
    return tuple(_BACKENDS.keys())


_BACKENDS: dict[str, AuthBackend] = {
    "passthrough": PassthroughAuthBackend(),
    "token": TokenAuthBackend(),
}
