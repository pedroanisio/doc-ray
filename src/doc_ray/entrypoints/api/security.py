"""Authentication + Row-Level-Security principal wiring for the API (D-010).

Authorization is enforced in the database by RLS (migration ``018_rbac.sql``), not
in application code. The API authenticates a request to a :class:`Principal`; when
that principal carries a database id, the request's connection is switched to the
non-superuser ``docray_client`` role with ``app.principal_id`` set, so RLS filters
every read. The default :class:`NoopAuthenticator` returns an anonymous principal
with no id, leaving the trusted (superuser) connection unfiltered — open by default,
auth-ready. Swap in a real ``Authenticator`` to enforce per-document access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import psycopg
from fastapi import Request

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
            anonymous request that runs unfiltered on the trusted connection.
        scopes: Coarse capability set (e.g. ``{"read"}``).
    """

    subject: str | None
    principal_id: str | None
    scopes: frozenset[str] = field(default_factory=frozenset)


#: The default identity: anonymous, unfiltered, read-only.
ANONYMOUS = Principal(subject=None, principal_id=None, scopes=frozenset({"read"}))


@runtime_checkable
class Authenticator(Protocol):
    """Resolve an inbound request to a :class:`Principal`."""

    async def authenticate(self, request: Request) -> Principal:
        """Return the principal for *request* (never raises for anonymous)."""
        ...


class NoopAuthenticator:
    """Default authenticator: every request is :data:`ANONYMOUS`."""

    async def authenticate(self, request: Request) -> Principal:
        """Return :data:`ANONYMOUS` regardless of the request."""
        return ANONYMOUS


async def apply_principal(conn: psycopg.AsyncConnection[Any], principal: Principal) -> None:
    """Bind *principal* to *conn* so RLS applies, if the principal has an id.

    A principal without an id is a no-op: the connection stays on its trusted
    (superuser) role and RLS is bypassed by design.
    """
    if principal.principal_id is None:
        return
    await conn.execute(f"SET ROLE {_CLIENT_ROLE}")
    await conn.execute(
        "SELECT set_config('app.principal_id', %s, false)", (principal.principal_id,)
    )


async def reset_principal(conn: psycopg.AsyncConnection[Any], principal: Principal) -> None:
    """Undo :func:`apply_principal` before the connection returns to the pool."""
    if principal.principal_id is None:
        return
    await conn.execute("SELECT set_config('app.principal_id', '', false)")
    await conn.execute("RESET ROLE")
