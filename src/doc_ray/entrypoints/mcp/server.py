"""Streamable-HTTP MCP server exposing the doc-ray corpus read tools to agentic AI.

The server is a thin, stateless front door: it registers the read tools from
:mod:`~doc_ray.entrypoints.mcp.corpus` on a :class:`~mcp.server.fastmcp.FastMCP`
instance, wraps the resulting Streamable-HTTP ASGI app with
:class:`~doc_ray.entrypoints.mcp.auth.BearerAuthMiddleware`, and forwards each
caller's token to the GraphQL API. It owns no database connection — every tool
result is exactly what the caller's principal may read under Postgres RLS.

Run it with any ASGI server, e.g.::

    uvicorn doc_ray.entrypoints.mcp.server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from doc_ray.config import Config
from doc_ray.entrypoints.mcp import audit, compose, corpus, mutations
from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware

#: Loopback patterns the SDK trusts by default — kept so local dev still works.
_LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
_LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def _public_hosts() -> list[str]:
    """Public ``Host`` values to accept (``DOCRAY_MCP_ALLOWED_HOSTS`` + ``DOCRAY_PUBLIC_BASE_URL``).

    Behind the Cloudflare tunnel the request arrives with the public host (e.g.
    ``doc-ray.com``), which the SDK's localhost-only DNS-rebind default rejects
    with HTTP 421 ("Invalid Host header"). This surfaces the deployed host(s) so
    they can be allow-listed without disabling the protection.
    """
    hosts = [
        h.strip() for h in os.environ.get("DOCRAY_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
    ]
    base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip()
    if base and (netloc := urlparse(base).netloc):
        hosts.append(netloc)
    return hosts


def _transport_security() -> TransportSecuritySettings | None:
    """DNS-rebind settings allowing the public host(s); ``None`` keeps the SDK localhost default."""
    hosts = _public_hosts()
    if not hosts:
        return None
    allowed_hosts = list(_LOCAL_HOSTS)
    allowed_origins = list(_LOCAL_ORIGINS)
    for host in hosts:
        allowed_hosts += [host, f"{host}:*"]
        allowed_origins += [f"https://{host}", f"https://{host}:*"]
    return TransportSecuritySettings(allowed_hosts=allowed_hosts, allowed_origins=allowed_origins)


def build_server(enable_write: bool = False) -> FastMCP:
    """Create a FastMCP server with the corpus read + composition-grounding tools.

    Every tool is wrapped by :func:`~doc_ray.entrypoints.mcp.audit.audited`, so
    each call emits one audit log line (caller fingerprint, outcome, duration).

    Args:
        enable_write: When true, additionally register the reprocessing + curation
            write tools (:data:`doc_ray.entrypoints.mcp.mutations.TOOLS`). Off by
            default so the advertised surface is read-only unless an operator opts
            in via ``DOCRAY_MCP_ENABLE_WRITE``; even when on, every write is still
            gated by the API's ``write`` scope and per-document ACL guards.
    """
    mcp = FastMCP("doc-ray")
    if (security := _transport_security()) is not None:
        # Accept the public Host behind the tunnel — the localhost-only default 421s.
        mcp.settings.transport_security = security
    tools = (*corpus.TOOLS, *compose.TOOLS, *(mutations.TOOLS if enable_write else ()))
    for tool in tools:
        mcp.tool()(audit.audited(tool))
    return mcp


def create_app(cfg: Config | None = None) -> Starlette:
    """Build the bearer-authenticated Streamable-HTTP ASGI app.

    Args:
        cfg: Runtime config; loaded from the environment when omitted.

    Returns:
        A Starlette app exposing the MCP endpoint behind :class:`BearerAuthMiddleware`.
    """
    cfg = cfg or Config.from_env()
    app = build_server(enable_write=cfg.mcp_enable_write).streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, cfg=cfg)
    return app


#: Module-level ASGI app for ``uvicorn doc_ray.entrypoints.mcp.server:app``.
app = create_app()
