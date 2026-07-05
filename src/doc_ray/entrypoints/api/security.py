"""Authentication + Row-Level-Security principal wiring for the API (D-010).

Authorization is enforced in the database by RLS (migration ``018_rbac.sql``), not
in application code. The API authenticates a request to a :class:`Principal`; each
HTTP request is switched to the non-superuser ``docray_api`` role before resolver
execution. Authenticated callers also get ``app.principal_id`` set, so RLS filters
every read/write to their document grants. Anonymous callers, when enabled, stay on
``docray_api`` with no principal id; they no longer run on the trusted superuser
connection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import psycopg
from fastapi import HTTPException, Request, status

# Trusted API role created by migration 030. Fixed literal (never user input),
# so interpolating it into SET ROLE is injection-safe. Direct SQL users inherit
# from docray_client under their own LOGIN role instead; only docray_api may
# assert app.principal_id.
_CLIENT_ROLE = "docray_api"


@dataclass(frozen=True)
class Principal:
    """An authenticated identity.

    Attributes:
        subject: External identity (OIDC ``sub`` / LDAP dn), or ``None``.
        principal_id: ``principals.id`` UUID used by RLS, or ``None`` for an
            anonymous request that runs on ``docray_api`` with no principal id.
        scopes: Coarse capability set (e.g. ``{"read"}``).
    """

    subject: str | None
    principal_id: str | None
    scopes: frozenset[str] = field(default_factory=frozenset)


#: The default identity: anonymous, RLS-scoped, read-only.
ANONYMOUS = Principal(subject=None, principal_id=None, scopes=frozenset({"read"}))


@runtime_checkable
class Authenticator(Protocol):
    """Resolve an inbound request to a :class:`Principal`."""

    async def authenticate(self, request: Request) -> Principal:
        """Return the principal for *request* (never raises for anonymous)."""
        ...


class NoopAuthenticator:
    """Test authenticator: every request is :data:`ANONYMOUS`."""

    async def authenticate(self, request: Request) -> Principal:
        """Return :data:`ANONYMOUS` regardless of the request."""
        return ANONYMOUS


class HeaderTokenAuthenticator:
    """Authenticate API requests with a configured bearer or X-DocRay token."""

    def __init__(
        self,
        *,
        tokens: Mapping[str, str | tuple[str, frozenset[str]]],
        allow_anonymous: bool = True,
    ) -> None:
        """Build a token authenticator.

        Args:
            tokens: Mapping of presented token to principal id, optionally with
                an explicit scope set.
            allow_anonymous: If true, missing credentials get anonymous
                RLS-scoped access. Invalid credentials always fail closed.
        """
        self._tokens = {
            token: value if isinstance(value, tuple) else (value, frozenset({"read", "write"}))
            for token, value in tokens.items()
        }
        self._allow_anonymous = allow_anonymous

    async def authenticate(self, request: Request) -> Principal:
        """Resolve ``Authorization: Bearer`` or ``X-DocRay-API-Token``."""
        token = _request_token(request)
        if token is None:
            if self._allow_anonymous:
                return ANONYMOUS
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            principal_id, scopes = self._tokens[token]
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        return Principal(subject=f"token:{token}", principal_id=principal_id, scopes=scopes)


def _request_token(request: Request) -> str | None:
    """Extract a bearer or explicit DocRay API token from request headers."""
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    header_token = request.headers.get("x-docray-api-token", "").strip()
    return header_token or None


async def apply_principal(conn: psycopg.AsyncConnection[Any], principal: Principal) -> None:
    """Bind *principal* to *conn* so RLS applies.

    Every API request drops to ``docray_api``. A principal with an id also stamps
    ``app.principal_id``; anonymous requests get no principal and therefore match
    only policies that intentionally allow anonymous access.
    """
    await conn.execute(f"SET ROLE {_CLIENT_ROLE}")
    if principal.principal_id is None:
        await conn.execute("SELECT set_config('app.principal_id', '', false)")
        return
    await conn.execute(
        "SELECT set_config('app.principal_id', %s, false)", (principal.principal_id,)
    )


async def reset_principal(conn: psycopg.AsyncConnection[Any], principal: Principal) -> None:
    """Undo :func:`apply_principal` before the connection returns to the pool."""
    await conn.execute("SELECT set_config('app.principal_id', '', false)")
    await conn.execute("RESET ROLE")
