"""OAuth 2.1 Authorization Server domain logic for the MCP connector flow.

doc-ray already issues RLS-bound API access JWTs (see
:mod:`doc_ray.entrypoints.api.security`); this module adds the OAuth 2.1
*front door* an MCP client such as Claude Desktop drives to obtain one without a
pre-shared token:

    Dynamic Client Registration (RFC 7591)
      -> /authorize  (browser login + consent, PKCE S256)
      -> /token      (authorization_code + PKCE  ->  access + refresh)

This service owns only the security-critical, storage-agnostic rules — client
registration validation, authorization-code issuance, and redemption with PKCE.
Token minting and the user-facing login/consent stay in their existing homes
(the API's JWT primitives and the webapp's session login), so RLS binding is
unchanged. Persistence is abstracted behind the store protocols below.

Deliberately strict, per the MCP authorization spec + OAuth 2.1:

* PKCE is **mandatory** and only ``S256`` is accepted (``plain`` is refused).
* ``redirect_uri`` is matched **exactly** against the client's registered set.
* redirect URIs must be ``https`` or loopback (``http://localhost`` / ``127.0.0.1``).
* Authorization codes are single-use, short-lived, and bound to the client,
  redirect URI, PKCE challenge, and resource.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlparse

#: Scopes doc-ray understands. Requests may ask for a subset; unknown scopes are
#: dropped rather than rejected (OAuth 2.1 lets the AS narrow the grant).
SUPPORTED_SCOPES = ("read",)
DEFAULT_SCOPE = "read"

#: Authorization codes are exchanged immediately; keep the window tight.
AUTHORIZATION_CODE_TTL = timedelta(seconds=60)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OAuthError(ValueError):
    """An OAuth error carrying a spec error code and HTTP status.

    ``redirectable`` marks errors that OAuth returns to the client via the
    redirect URI (per OAuth 2.1 §4.1.2.1) rather than shown directly — but only
    once the client and redirect URI are themselves validated.
    """

    def __init__(
        self,
        error: str,
        description: str = "",
        *,
        status_code: int = 400,
        redirectable: bool = False,
    ) -> None:
        """Initialize the OAuth error."""
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status_code = status_code
        self.redirectable = redirectable


@dataclass(frozen=True)
class RegisteredClient:
    """A dynamically registered OAuth client."""

    client_id: str
    redirect_uris: tuple[str, ...]
    client_name: str | None = None
    token_endpoint_auth_method: str = "none"
    grant_types: tuple[str, ...] = ("authorization_code", "refresh_token")
    scope: str = DEFAULT_SCOPE
    client_secret: str | None = None


@dataclass(frozen=True)
class AuthorizationRequest:
    """A validated ``/authorize`` request, ready to show consent for."""

    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scope: str
    state: str | None
    resource: str | None
    client_name: str | None


@dataclass(frozen=True)
class AuthorizationGrant:
    """The principal a redeemed authorization code authorizes tokens for."""

    principal_id: str
    subject: str
    scopes: frozenset[str]
    resource: str | None


class OAuthClientStore(Protocol):
    """Persistence for dynamically registered clients."""

    def create_client(self, client: RegisteredClient, *, secret_hash: str | None) -> None:
        """Persist a newly registered client."""
        ...

    def get_client(self, client_id: str) -> RegisteredClient | None:
        """Return a registered client by id, or ``None`` (never the secret)."""
        ...

    def verify_client_secret(self, client_id: str, secret: str) -> bool:
        """Return whether *secret* matches the stored hash for a confidential client."""
        ...


class AuthorizationCodeStore(Protocol):
    """Persistence for short-lived, single-use authorization codes."""

    def store_code(self, *, code_hash: str, record: dict[str, Any]) -> None:
        """Persist a hashed authorization code and its bound context."""
        ...

    def consume_code(self, *, code_hash: str, now: datetime) -> dict[str, Any] | None:
        """Atomically consume (single-use) a code, returning its record or ``None``."""
        ...


class OAuthServerService:
    """OAuth 2.1 authorization-code + PKCE flow, storage-agnostic."""

    def __init__(
        self,
        clients: OAuthClientStore,
        codes: AuthorizationCodeStore,
        *,
        now: Callable[[], datetime] | None = None,
        code_factory: Callable[[], str] | None = None,
        client_id_factory: Callable[[], str] | None = None,
    ) -> None:
        """Initialize the OAuth server service."""
        self._clients = clients
        self._codes = codes
        self._now = now or (lambda: datetime.now(UTC))
        self._code_factory = code_factory or (lambda: secrets.token_urlsafe(40))
        self._client_id_factory = client_id_factory or (
            lambda: f"docray-{secrets.token_urlsafe(18)}"
        )

    # -- Dynamic Client Registration (RFC 7591) ------------------------------

    def register_client(self, metadata: dict[str, Any]) -> RegisteredClient:
        """Validate and persist a dynamic client registration."""
        raw_uris = metadata.get("redirect_uris")
        if not isinstance(raw_uris, list) or not raw_uris:
            raise OAuthError("invalid_client_metadata", "redirect_uris is required")
        redirect_uris = tuple(str(u) for u in raw_uris)
        for uri in redirect_uris:
            _validate_redirect_uri(uri)

        auth_method = str(metadata.get("token_endpoint_auth_method") or "none")
        if auth_method not in {"none", "client_secret_post", "client_secret_basic"}:
            raise OAuthError("invalid_client_metadata", "unsupported token_endpoint_auth_method")

        requested_grants = metadata.get("grant_types") or ["authorization_code", "refresh_token"]
        grant_types = tuple(
            g for g in requested_grants if g in {"authorization_code", "refresh_token"}
        )
        if "authorization_code" not in grant_types:
            raise OAuthError("invalid_client_metadata", "authorization_code grant is required")

        scope = _narrow_scope(metadata.get("scope"))
        client_name = metadata.get("client_name")
        secret = secrets.token_urlsafe(32) if auth_method != "none" else None
        client = RegisteredClient(
            client_id=self._client_id_factory(),
            redirect_uris=redirect_uris,
            client_name=str(client_name) if client_name else None,
            token_endpoint_auth_method=auth_method,
            grant_types=grant_types,
            scope=scope,
            client_secret=secret,
        )
        self._clients.create_client(
            client, secret_hash=_sha256(secret) if secret is not None else None
        )
        return client

    def get_client(self, client_id: str) -> RegisteredClient | None:
        """Return a registered client by id."""
        return self._clients.get_client(client_id)

    # -- Authorization endpoint (/authorize) ---------------------------------

    def validate_authorization_request(self, params: dict[str, str]) -> AuthorizationRequest:
        """Validate an ``/authorize`` query before authenticating the user.

        Client and redirect URI are validated first; only after they check out
        may other errors be returned to the client via the redirect URI.
        """
        client_id = params.get("client_id", "")
        client = self._clients.get_client(client_id) if client_id else None
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id", status_code=400)
        redirect_uri = params.get("redirect_uri", "")
        if redirect_uri not in client.redirect_uris:
            raise OAuthError("invalid_request", "redirect_uri is not registered", status_code=400)

        # From here, errors are redirectable to the (now-trusted) redirect_uri.
        if params.get("response_type") != "code":
            raise OAuthError(
                "unsupported_response_type",
                "only response_type=code is supported",
                redirectable=True,
            )
        code_challenge = params.get("code_challenge", "")
        if not code_challenge:
            raise OAuthError(
                "invalid_request", "PKCE code_challenge is required", redirectable=True
            )
        method = params.get("code_challenge_method", "plain")
        if method != "S256":
            raise OAuthError(
                "invalid_request", "code_challenge_method must be S256", redirectable=True
            )
        scope = _narrow_scope(params.get("scope")) if params.get("scope") else client.scope
        return AuthorizationRequest(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=method,
            scope=scope,
            state=params.get("state"),
            resource=params.get("resource"),
            client_name=client.client_name,
        )

    def issue_code(self, request: AuthorizationRequest, *, principal_id: str, subject: str) -> str:
        """Issue an authorization code for a consented request; returns the raw code."""
        raw_code = self._code_factory()
        now = self._now()
        self._codes.store_code(
            code_hash=_sha256(raw_code),
            record={
                "client_id": request.client_id,
                "redirect_uri": request.redirect_uri,
                "code_challenge": request.code_challenge,
                "code_challenge_method": request.code_challenge_method,
                "principal_id": principal_id,
                "subject": subject,
                "scope": request.scope,
                "resource": request.resource,
                "expires_at": now + AUTHORIZATION_CODE_TTL,
            },
        )
        return raw_code

    # -- Token endpoint (/token) ---------------------------------------------

    def exchange_authorization_code(self, params: dict[str, str]) -> AuthorizationGrant:
        """Redeem an authorization code (with PKCE) into a grant to mint tokens for."""
        client_id = params.get("client_id", "")
        client = self._clients.get_client(client_id) if client_id else None
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id", status_code=401)
        self._authenticate_client(client, params)

        code = params.get("code", "")
        record = (
            self._codes.consume_code(code_hash=_sha256(code), now=self._now()) if code else None
        )
        if record is None:
            raise OAuthError("invalid_grant", "authorization code is invalid or expired")
        if record.get("client_id") != client_id:
            raise OAuthError("invalid_grant", "authorization code was issued to another client")
        if record.get("redirect_uri") != params.get("redirect_uri", ""):
            raise OAuthError("invalid_grant", "redirect_uri mismatch")

        verifier = params.get("code_verifier", "")
        if not verifier or not _verify_pkce(verifier, str(record.get("code_challenge"))):
            raise OAuthError("invalid_grant", "PKCE verification failed")

        scope = str(record.get("scope") or DEFAULT_SCOPE)
        return AuthorizationGrant(
            principal_id=str(record["principal_id"]),
            subject=str(record["subject"]),
            scopes=frozenset(scope.split()),
            resource=(str(record["resource"]) if record.get("resource") else None),
        )

    def _authenticate_client(self, client: RegisteredClient, params: dict[str, str]) -> None:
        """Authenticate a confidential client at the token endpoint.

        Public clients (``token_endpoint_auth_method="none"`` — what MCP clients
        register as) are authenticated by PKCE alone; confidential clients must
        present the secret, verified against its stored hash by the store.
        """
        if client.token_endpoint_auth_method == "none":
            return
        secret = params.get("client_secret", "")
        if not secret or not self._clients.verify_client_secret(client.client_id, secret):
            raise OAuthError("invalid_client", "client authentication failed", status_code=401)


# -- helpers -----------------------------------------------------------------


def _validate_redirect_uri(uri: str) -> None:
    parsed = urlparse(uri)
    if parsed.fragment:
        raise OAuthError("invalid_redirect_uri", "redirect_uri must not contain a fragment")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and (parsed.hostname in _LOOPBACK_HOSTS):
        return
    raise OAuthError(
        "invalid_redirect_uri", "redirect_uri must be https or a loopback http address"
    )


def _narrow_scope(requested: object) -> str:
    if not requested:
        return DEFAULT_SCOPE
    granted = [s for s in str(requested).split() if s in SUPPORTED_SCOPES]
    return " ".join(granted) if granted else DEFAULT_SCOPE


def _verify_pkce(verifier: str, challenge: str) -> bool:
    """Return whether S256(verifier) equals the stored code_challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, challenge)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_hex(value: str) -> str:
    """Public helper so adapters hash client secrets the same way the service does."""
    return _sha256(value)


__all__ = [
    "OAuthServerService",
    "OAuthClientStore",
    "AuthorizationCodeStore",
    "OAuthError",
    "RegisteredClient",
    "AuthorizationRequest",
    "AuthorizationGrant",
    "SUPPORTED_SCOPES",
    "DEFAULT_SCOPE",
    "AUTHORIZATION_CODE_TTL",
]
