"""OIDC bearer-token authentication for the API (BL-066)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
from fastapi import HTTPException, Request, status

from doc_ray.entrypoints.api.security import Authenticator, Principal

PrincipalResolver = Callable[
    [str, dict[str, Any]], Principal | None | Awaitable[Principal | None]
]


class OIDCAuthenticator:
    """Authenticate OIDC bearer JWTs against a configured JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str | None,
        jwks: Mapping[str, Any],
        resolve_principal: PrincipalResolver,
        fallback: Authenticator,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        """Build an OIDC-first authenticator.

        Non-JWT credentials are delegated to *fallback* so configured static
        tokens remain available for local/break-glass use.
        """
        self._issuer = issuer
        self._audience = audience
        self._jwks = jwks
        self._resolve_principal = resolve_principal
        self._fallback = fallback
        self._now = now or (lambda: datetime.now(UTC))

    async def authenticate(self, request: Request) -> Principal:
        """Resolve an OIDC bearer JWT or delegate non-JWT credentials."""
        token = _request_token(request)
        if token is None or token.count(".") != 2:
            return await self._fallback.authenticate(request)
        try:
            claims = verify_oidc_jwt(
                token,
                issuer=self._issuer,
                audience=self._audience,
                jwks=self._jwks,
                now=self._now(),
            )
            subject = claims["sub"]
            if not isinstance(subject, str) or not subject:
                raise ValueError("OIDC sub is required")
            resolved = self._resolve_principal(subject, claims)
            principal = await resolved if inspect.isawaitable(resolved) else resolved
            if principal is None or principal.principal_id is None:
                raise ValueError("OIDC subject is not mapped to a principal")
            return principal
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid OIDC token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc


class OIDCPrincipalPool(Protocol):
    """Pool surface needed to resolve OIDC principals."""

    def connection(
        self,
    ) -> AbstractAsyncContextManager[psycopg.AsyncConnection[Mapping[str, object]]]:
        """Return an async connection context manager."""
        ...


class PostgresOIDCPrincipalResolver:
    """Resolve OIDC ``sub`` claims through ``principals.external_subject``."""

    def __init__(self, pool: OIDCPrincipalPool, *, auto_create: bool = False) -> None:
        """Store an async psycopg pool-like object."""
        self._pool = pool
        self._auto_create = auto_create

    async def __call__(self, subject: str, claims: dict[str, Any]) -> Principal | None:
        """Return the principal matching *subject*, or None when unmapped."""
        async with self._pool.connection() as raw_conn:
            cur = await raw_conn.execute(
                "SELECT id FROM principals WHERE external_subject = %s",
                (subject,),
            )
            row = await cur.fetchone()
            if row is None and self._auto_create:
                cur = await raw_conn.execute(
                    "INSERT INTO principals (kind, display_name, external_subject) "
                    "VALUES ('human', %s, %s) RETURNING id",
                    (_oidc_display_name(subject, claims), subject),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        principal_id = row["id"] if isinstance(row, Mapping) else row[0]
        return Principal(
            subject=f"oidc:{subject}",
            principal_id=str(principal_id),
            scopes=_oidc_claim_scopes(claims),
        )


def verify_oidc_jwt(
    token: str,
    *,
    issuer: str,
    audience: str | None,
    jwks: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify an OIDC JWT against *jwks* and return its claims."""
    try:
        header_segment, claims_segment, signature_segment = token.split(".", 2)
    except ValueError as exc:
        raise ValueError("OIDC JWT must have three segments") from exc
    header = _b64url_json_loads(header_segment)
    claims = _b64url_json_loads(claims_segment)
    alg = header.get("alg")
    if alg not in {"HS256", "RS256"}:
        raise ValueError("unsupported OIDC JWT alg")
    key = _jwk_for_header(jwks, header)
    signing_input = f"{header_segment}.{claims_segment}"
    signature = _b64url_decode(signature_segment)
    if alg == "HS256":
        _verify_hs256_jwk(signing_input, signature, key)
    else:
        _verify_rs256_jwk(signing_input, signature, key)
    _validate_oidc_claims(claims, issuer=issuer, audience=audience, now=now)
    return claims


def _request_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return request.headers.get("x-docray-api-token", "").strip() or None


def _oidc_display_name(subject: str, claims: Mapping[str, Any]) -> str:
    for key in ("preferred_username", "email", "name"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"oidc:{subject}"


def _jwk_for_header(jwks: Mapping[str, Any], header: Mapping[str, Any]) -> Mapping[str, Any]:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise ValueError("JWKS keys must be a list")
    kid = header.get("kid")
    alg = header.get("alg")
    matches = [
        key
        for key in keys
        if isinstance(key, Mapping)
        and (kid is None or key.get("kid") == kid)
        and (key.get("alg") in {None, alg})
    ]
    if len(matches) != 1:
        raise ValueError("OIDC JWT key not found")
    return matches[0]


def _verify_hs256_jwk(signing_input: str, signature: bytes, key: Mapping[str, Any]) -> None:
    if key.get("kty") != "oct" or not isinstance(key.get("k"), str):
        raise ValueError("HS256 OIDC JWT requires an oct JWK")
    secret = _b64url_decode(str(key["k"]))
    expected = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("OIDC JWT signature mismatch")


_RSA_SHA256_DER_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _verify_rs256_jwk(signing_input: str, signature: bytes, key: Mapping[str, Any]) -> None:
    if key.get("kty") != "RSA" or not isinstance(key.get("n"), str) or not isinstance(
        key.get("e"), str
    ):
        raise ValueError("RS256 OIDC JWT requires an RSA JWK")
    n = int.from_bytes(_b64url_decode(str(key["n"])), "big")
    e = int.from_bytes(_b64url_decode(str(key["e"])), "big")
    key_len = (n.bit_length() + 7) // 8
    if len(signature) != key_len:
        raise ValueError("OIDC JWT signature length mismatch")
    digest_info = _RSA_SHA256_DER_PREFIX + hashlib.sha256(signing_input.encode()).digest()
    expected = b"\x00\x01" + (b"\xff" * (key_len - len(digest_info) - 3)) + b"\x00" + digest_info
    actual = pow(int.from_bytes(signature, "big"), e, n).to_bytes(key_len, "big")
    if not hmac.compare_digest(actual, expected):
        raise ValueError("OIDC JWT signature mismatch")


def _validate_oidc_claims(
    claims: Mapping[str, Any], *, issuer: str, audience: str | None, now: datetime | None
) -> None:
    if claims.get("iss") != issuer:
        raise ValueError("OIDC issuer mismatch")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("OIDC sub is required")
    current = int(_as_utc(now or datetime.now(UTC)).timestamp())
    if not isinstance(claims.get("exp"), int) or claims["exp"] <= current:
        raise ValueError("OIDC JWT expired")
    if audience is not None:
        token_audience = claims.get("aud")
        if isinstance(token_audience, str):
            ok = token_audience == audience
        elif isinstance(token_audience, list):
            ok = audience in token_audience
        else:
            ok = False
        if not ok:
            raise ValueError("OIDC audience mismatch")


def _oidc_claim_scopes(claims: Mapping[str, Any]) -> frozenset[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        scopes = frozenset(part for part in scope.split() if part)
        return scopes or frozenset({"read"})
    scp = claims.get("scp")
    if isinstance(scp, list) and all(isinstance(item, str) for item in scp):
        return frozenset(scp) or frozenset({"read"})
    return frozenset({"read"})


def _b64url_json_loads(segment: str) -> dict[str, Any]:
    raw = _b64url_decode(segment)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JWT segment must be a JSON object")
    return value


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
