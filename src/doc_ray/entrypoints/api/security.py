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
import logging
import uuid
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
_LOGGER = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class RefreshTokenRecord:
    """Server-side state for one issued refresh token."""

    token_id: str
    family_id: str
    principal: Principal
    expires_at: datetime
    status: str = "active"


@runtime_checkable
class RefreshTokenStore(Protocol):
    """Rotation-state surface shared by the in-memory and Postgres stores."""

    def remember(self, record: RefreshTokenRecord) -> None:
        """Record a newly-issued active refresh token."""
        ...

    def consume(self, token_id: str, *, now: datetime) -> RefreshTokenRecord:
        """Consume one active token; reuse revokes its family before raising."""
        ...

    def revoke_family(self, family_id: str) -> None:
        """Mark every token in *family_id* revoked."""
        ...


class InMemoryRefreshTokenStore:
    """Process-local refresh token rotation state.

    This keeps rotation semantics explicit and testable. Deployments that need
    refresh tokens across process restarts can replace this with a durable store
    behind the same small method surface.
    """

    def __init__(self) -> None:
        """Initialize an empty refresh-token registry."""
        self._records: dict[str, RefreshTokenRecord] = {}

    def remember(self, record: RefreshTokenRecord) -> None:
        """Record a newly-issued active refresh token."""
        self._records[record.token_id] = record

    def consume(self, token_id: str, *, now: datetime) -> RefreshTokenRecord:
        """Consume one active refresh token, marking it rotated.

        Reuse of a rotated/revoked token revokes the whole family before
        raising, which is the detection behavior BL-072 requires.
        """
        record = self._records.get(token_id)
        if record is None:
            raise ValueError("refresh token is unknown")
        if record.expires_at <= _as_utc(now):
            self._records[token_id] = RefreshTokenRecord(
                record.token_id,
                record.family_id,
                record.principal,
                record.expires_at,
                "expired",
            )
            raise ValueError("refresh token expired")
        if record.status != "active":
            self.revoke_family(record.family_id)
            _LOGGER.warning(
                "refresh token reuse detected; revoked family %s for principal %s",
                record.family_id,
                record.principal.principal_id,
            )
            raise ValueError("refresh token reused")
        self._records[token_id] = RefreshTokenRecord(
            record.token_id,
            record.family_id,
            record.principal,
            record.expires_at,
            "rotated",
        )
        return record

    def revoke_family(self, family_id: str) -> None:
        """Mark every token in *family_id* revoked."""
        for token_id, record in list(self._records.items()):
            if record.family_id == family_id:
                self._records[token_id] = RefreshTokenRecord(
                    record.token_id,
                    record.family_id,
                    record.principal,
                    record.expires_at,
                    "revoked",
                )


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
        return _apply_act_as(
            request,
            Principal(subject=f"token:{token}", principal_id=principal_id, scopes=scopes),
        )


def _apply_act_as(request: Request, principal: Principal) -> Principal:
    """Delegate a static-token principal to the ``X-DocRay-Act-As`` identity.

    Trusted service callers (the webapp) authenticate with their own token but
    act on behalf of the logged-in browser user, so RLS and DB-side
    authorization apply to that USER rather than the service token's (often
    admin-grade) principal. Delegation requires the explicit ``impersonate``
    scope on the token (``DOCRAY_API_TOKENS=token=uuid:read+write+impersonate``)
    and is never available to JWT/anonymous callers. Both identities stay in
    ``subject`` for the audit trail; the ``impersonate`` capability itself is
    not forwarded.
    """
    act_as = request.headers.get("x-docray-act-as", "").strip()
    if not act_as:
        return principal
    if "impersonate" not in principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this API token may not act on behalf of other principals",
        )
    try:
        acted_principal_id = str(uuid.UUID(act_as))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-DocRay-Act-As must be a principal UUID",
        ) from exc
    return Principal(
        subject=f"{principal.subject}#act-as:{acted_principal_id}",
        principal_id=acted_principal_id,
        scopes=frozenset(principal.scopes - {"impersonate"}),
    )


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
        "jti": str(uuid.uuid4()),
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


def issue_refresh_token(
    principal: Principal,
    *,
    secret: str,
    issuer: str,
    audience: str | None,
    ttl_seconds: int,
    store: RefreshTokenStore,
    now: datetime | None = None,
    family_id: str | None = None,
) -> str:
    """Issue and remember a refresh token for *principal*."""
    if not secret:
        raise ValueError("JWT secret is required")
    if principal.principal_id is None:
        raise ValueError("refresh token principal_id is required")
    issued_at = _as_utc(now or datetime.now(UTC))
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    token_id = str(uuid.uuid4())
    refresh_family_id = family_id or str(uuid.uuid4())
    claims: dict[str, Any] = {
        "iss": issuer,
        "sub": principal.subject or principal.principal_id,
        "principal_id": principal.principal_id,
        "scopes": sorted(principal.scopes),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": token_id,
        "family_id": refresh_family_id,
        "typ": "refresh",
    }
    if audience is not None:
        claims["aud"] = audience
    signing_input = ".".join((_b64url_json({"alg": "HS256", "typ": "JWT"}), _b64url_json(claims)))
    token = f"{signing_input}.{_sign(signing_input, secret)}"
    store.remember(
        RefreshTokenRecord(
            token_id=token_id,
            family_id=refresh_family_id,
            principal=Principal(
                subject=str(claims["sub"]),
                principal_id=principal.principal_id,
                scopes=principal.scopes,
            ),
            expires_at=expires_at,
        )
    )
    return token


def rotate_refresh_token(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str | None,
    ttl_seconds: int,
    access_ttl_seconds: int,
    store: RefreshTokenStore,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Consume *token* and return a fresh ``(access_token, refresh_token)`` pair."""
    claims = _verify_refresh_token_claims(
        token, secret=secret, issuer=issuer, audience=audience, now=now
    )
    current = _as_utc(now or datetime.now(UTC))
    token_id = str(claims["jti"])
    record = store.consume(token_id, now=current)
    access_token = issue_access_jwt(
        record.principal,
        secret=secret,
        issuer=issuer,
        audience=audience,
        ttl_seconds=access_ttl_seconds,
        now=current,
    )
    refresh_token = issue_refresh_token(
        record.principal,
        secret=secret,
        issuer=issuer,
        audience=audience,
        ttl_seconds=ttl_seconds,
        store=store,
        now=current,
        family_id=record.family_id,
    )
    return access_token, refresh_token


def _verify_refresh_token_claims(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str | None,
    now: datetime | None,
) -> dict[str, Any]:
    try:
        header_segment, claims_segment, signature = token.split(".", 2)
    except ValueError as exc:
        raise ValueError("refresh token must have three segments") from exc
    signing_input = f"{header_segment}.{claims_segment}"
    if not hmac.compare_digest(signature, _sign(signing_input, secret)):
        raise ValueError("refresh token signature mismatch")
    header = _b64url_json_loads(header_segment)
    claims = _b64url_json_loads(claims_segment)
    if header.get("alg") != "HS256" or claims.get("typ") != "refresh":
        raise ValueError("unsupported refresh token")
    if claims.get("iss") != issuer:
        raise ValueError("refresh token issuer mismatch")
    if audience is not None and claims.get("aud") != audience:
        raise ValueError("refresh token audience mismatch")
    current = int(_as_utc(now or datetime.now(UTC)).timestamp())
    if not isinstance(claims.get("exp"), int) or claims["exp"] <= current:
        raise ValueError("refresh token expired")
    for key in ("jti", "family_id", "principal_id", "sub", "scopes"):
        if key not in claims:
            raise ValueError(f"refresh token missing {key}")
    if not isinstance(claims["scopes"], list):
        raise ValueError("refresh token scopes must be a list")
    return claims


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
    raw = _b64url_decode(segment)
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


def _b64url_decode(segment: str) -> bytes:
    """Decode base64url data with optional omitted padding."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


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
