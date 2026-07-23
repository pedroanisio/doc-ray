"""Bearer/JWT authentication for the MCP server, reusing the GraphQL API's credentials.

Every MCP request is authenticated exactly like a GraphQL API request: a known static
token (``DOCRAY_API_TOKENS``) or a valid HS256 access JWT. Rejecting junk here returns a
clean 401 before any GraphQL round-trip; a valid token is stashed in
:data:`~doc_ray.entrypoints.mcp.tokens.caller_token` as the fallback for transports with no
per-call HTTP request. On Streamable-HTTP the tools re-read the credential from the live
request via :func:`~doc_ray.entrypoints.mcp.tokens.current_token` so a mid-session token
refresh keeps reaching ``/graphql`` (which re-validates and binds RLS). Unknown-token
semantics and the anonymous path mirror ``HeaderTokenAuthenticator``.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from doc_ray.config import Config
from doc_ray.entrypoints.api.security import verify_access_jwt
from doc_ray.entrypoints.mcp.tokens import bearer_from_request, caller_token


def _www_authenticate_challenge() -> str:
    """Build the RFC 9728 ``WWW-Authenticate`` value pointing at RS metadata.

    Directs an OAuth-capable client (e.g. Claude Desktop) to the Protected
    Resource Metadata document, which names the authorization server — turning a
    bare 401 into the start of the OAuth 2.1 sign-in flow.
    """
    url = os.environ.get("DOCRAY_MCP_RESOURCE_METADATA_URL", "").strip()
    if not url:
        base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
        if base:
            url = f"{base}/.well-known/oauth-protected-resource"
    return f'Bearer resource_metadata="{url}"' if url else "Bearer"


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate every MCP request the same way the GraphQL API does."""

    def __init__(self, app: ASGIApp, cfg: Config) -> None:
        """Capture the token map and JWT settings from the runtime config."""
        super().__init__(app)
        self._tokens = cfg.api_tokens
        self._allow_anonymous = cfg.api_allow_anonymous
        self._jwt_secret = cfg.api_jwt_secret
        self._jwt_issuer = cfg.api_jwt_issuer
        self._jwt_audience = cfg.api_jwt_audience

    def _valid(self, token: str) -> bool:
        """Return whether *token* is a known static token or a valid HS256 access JWT."""
        if token in self._tokens:
            return True
        secret = self._jwt_secret
        if secret is not None and token.count(".") == 2:
            try:
                verify_access_jwt(
                    token, secret=secret, issuer=self._jwt_issuer, audience=self._jwt_audience
                )
            except ValueError:
                return False
            return True
        return False

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Reject bad credentials with 401; stash a valid token for the tools to forward."""
        token = bearer_from_request(request)
        if token is None:
            if not self._allow_anonymous:
                return JSONResponse(
                    {"error": "authentication required"},
                    status_code=401,
                    headers={"WWW-Authenticate": _www_authenticate_challenge()},
                )
        elif not self._valid(token):
            return JSONResponse(
                {"error": "invalid token"},
                status_code=401,
                headers={"WWW-Authenticate": _www_authenticate_challenge()},
            )
        reset = caller_token.set(token or "")
        try:
            return await call_next(request)
        finally:
            caller_token.reset(reset)
