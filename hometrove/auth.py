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
    """

    name = getattr(settings, "auth_backend", "passthrough") or "passthrough"
    name = str(name).strip().lower()
    registry: Mapping[str, AuthBackend] = overrides or _BACKENDS
    backend = registry.get(name)
    if backend is not None:
        return backend
    return PassthroughAuthBackend()


def available_backends() -> Iterable[str]:
    """Names accepted by ``build_auth_backend`` (for diagnostics / docs)."""
    return tuple(_BACKENDS.keys())


_BACKENDS: dict[str, AuthBackend] = {
    "passthrough": PassthroughAuthBackend(),
}
