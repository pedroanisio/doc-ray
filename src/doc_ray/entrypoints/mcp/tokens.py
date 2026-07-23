"""Resolve the bearer token to forward to GraphQL for the current MCP tool call.

Precedence: the credential on the HTTP request the MCP server is *currently*
dispatching (read live from the SDK's per-message request context), else the
token stashed by :class:`~doc_ray.entrypoints.mcp.auth.BearerAuthMiddleware` at
validation time.

Why the request wins over the contextvar: in stateful Streamable-HTTP the tool
coroutine runs inside the session task created at ``initialize``, not the POST
task the middleware ran in. A contextvar set by the middleware therefore never
reaches the tool, and any token captured at connect time freezes there — once
the client rotates a refreshed credential, the MCP hop keeps returning 200 while
every forwarded GraphQL call carries the dead token and 401s. Reading the live
request keeps the refreshed credential flowing. The contextvar remains the
fallback for transports with no per-call HTTP request (e.g. stdio).

The middleware still authenticates every POST before dispatch, so the header
read here has already been validated for this request; this module only selects
*which* validated credential to forward, never whether to trust it.
"""

from __future__ import annotations

import contextvars

from starlette.requests import Request

#: Fallback token for transports without a per-call HTTP request (e.g. stdio),
#: set by ``BearerAuthMiddleware``. Empty = anonymous (no Authorization header).
caller_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "docray_mcp_caller_token", default=""
)


def bearer_from_request(request: Request) -> str | None:
    """Return the token from ``Authorization: Bearer`` or ``X-DocRay-API-Token``, else None.

    Shared by the auth middleware (which validates it) and the tools (which
    forward it), so the two never disagree on how a credential is read.
    """
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and auth[7:].strip():
        return auth[7:].strip()
    return request.headers.get("x-docray-api-token") or None


def _current_request() -> Request | None:
    """The Starlette request the MCP server is dispatching, or None (no HTTP transport)."""
    from mcp.server.lowlevel.server import request_ctx

    try:
        ctx = request_ctx.get()
    except LookupError:
        return None
    request = getattr(ctx, "request", None)
    return request if isinstance(request, Request) else None


def current_token() -> str:
    """Return the bearer token to forward for the call being dispatched.

    Prefers the live HTTP request's credential over the session-scoped
    :data:`caller_token`, so a client that rotated its token mid-session keeps
    authenticating downstream. Empty string means forward no ``Authorization``
    header (anonymous); a present-but-anonymous request never falls back to a
    stale session token.
    """
    request = _current_request()
    if request is not None:
        return bearer_from_request(request) or ""
    return caller_token.get()
