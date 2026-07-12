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

import base64
import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
        jwt_secret: str | None = None,
        jwt_issuer: str = "doc-ray",
        jwt_audience: str | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Build a token authenticator.

        Args:
            tokens: Mapping of presented token to principal id, optionally with
                an explicit scope set.
            allow_anonymous: If true, missing credentials get anonymous
                RLS-scoped access. Invalid credentials always fail closed.
            jwt_secret: Optional HMAC secret for locally issued short-lived
                access JWTs.
            jwt_issuer: Required issuer for JWT verification.
            jwt_audience: Optional required audience for JWT verification.
            now: Clock injection for deterministic expiry tests.
        """
        self._tokens = {
            token: value if isinstance(value, tuple) else (value, frozenset({"read", "write"}))
            for token, value in tokens.items()
        }
        self._allow_anonymous = allow_anonymous
        self._jwt_secret = jwt_secret
        self._jwt_issuer = jwt_issuer
        self._jwt_audience = jwt_audience
        self._now = now or (lambda: datetime.now(UTC))

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
            if self._jwt_secret and _looks_like_jwt(token):
                try:
                    return verify_access_jwt(
                        token,
                        secret=self._jwt_secret,
                        issuer=self._jwt_issuer,
                        audience=self._jwt_audience,
                        now=self._now(),
                    )
                except ValueError as jwt_exc:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid API token",
                        headers={"WWW-Authenticate": "Bearer"},
                    ) from jwt_exc
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        return Principal(subject=f"token:{token}", principal_id=principal_id, scopes=scopes)


def issue_access_jwt(
    principal: Principal,
    *,
    secret: str,
    issuer: str,
    audience: str | None,
    ttl_seconds: int,
    now: datetime | None = None,
) -> str:
    """Issue a short-lived HS256 API access JWT for *principal*.

    The token is intentionally self-contained so API request authentication can
    bind RLS without a database lookup on every request.
    """
    if not secret:
        raise ValueError("JWT secret is required")
    if principal.principal_id is None:
        raise ValueError("JWT principal_id is required")
    issued_at = _as_utc(now or datetime.now(UTC))
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": principal.subject or principal.principal_id,
        "principal_id": principal.principal_id,
        "scopes": sorted(principal.scopes),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "typ": "access",
    }
    if audience is not None:
        claims["aud"] = audience
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        (
            _b64url_json(header),
            _b64url_json(claims),
        )
    )
    signature = _sign(signing_input, secret)
    return f"{signing_input}.{signature}"


def verify_access_jwt(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str | None,
    now: datetime | None = None,
) -> Principal:
    """Verify a short-lived API access JWT and return its principal."""
    try:
        header_segment, claims_segment, signature = token.split(".", 2)
    except ValueError as exc:
        raise ValueError("JWT must have three segments") from exc
    signing_input = f"{header_segment}.{claims_segment}"
    expected = _sign(signing_input, secret)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("JWT signature mismatch")
    header = _b64url_json_loads(header_segment)
    claims = _b64url_json_loads(claims_segment)
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise ValueError("unsupported JWT header")
    if claims.get("typ") != "access":
        raise ValueError("unsupported JWT token type")
    if claims.get("iss") != issuer:
        raise ValueError("JWT issuer mismatch")
    if audience is not None and claims.get("aud") != audience:
        raise ValueError("JWT audience mismatch")
    current = int(_as_utc(now or datetime.now(UTC)).timestamp())
    if not isinstance(claims.get("exp"), int) or claims["exp"] <= current:
        raise ValueError("JWT expired")
    principal_id = claims.get("principal_id")
    subject = claims.get("sub")
    scopes = claims.get("scopes")
    if not isinstance(principal_id, str) or not principal_id:
        raise ValueError("JWT principal_id is required")
    if not isinstance(subject, str) or not subject:
        raise ValueError("JWT sub is required")
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise ValueError("JWT scopes must be a string list")
    return Principal(
        subject=f"jwt:{subject}",
        principal_id=principal_id,
        scopes=frozenset(scopes),
    )


def _looks_like_jwt(token: str) -> bool:
    """Return true when *token* has compact-JWT shape."""
    return token.count(".") == 2


def _as_utc(value: datetime) -> datetime:
    """Normalize datetimes to timezone-aware UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _b64url_json(value: Mapping[str, Any]) -> str:
    """Encode JSON using compact base64url without padding."""
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return _b64url(raw)


def _b64url_json_loads(segment: str) -> dict[str, Any]:
    """Decode a compact base64url JSON object."""
    raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JWT segment must be a JSON object")
    return value


def _sign(signing_input: str, secret: str) -> str:
    """Return the compact HS256 signature for *signing_input*."""
    digest = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return _b64url(digest)


def _b64url(raw: bytes) -> str:
    """Encode bytes using base64url without padding."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


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
