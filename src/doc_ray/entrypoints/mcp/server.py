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

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

from doc_ray.config import Config
from doc_ray.entrypoints.mcp import corpus
from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware


def build_server() -> FastMCP:
    """Create a FastMCP server with the corpus read tools registered."""
    mcp = FastMCP("doc-ray")
    for tool in corpus.TOOLS:
        mcp.tool()(tool)
    return mcp


def create_app(cfg: Config | None = None) -> Starlette:
    """Build the bearer-authenticated Streamable-HTTP ASGI app.

    Args:
        cfg: Runtime config; loaded from the environment when omitted.

    Returns:
        A Starlette app exposing the MCP endpoint behind :class:`BearerAuthMiddleware`.
    """
    cfg = cfg or Config.from_env()
    app = build_server().streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, cfg=cfg)
    return app


#: Module-level ASGI app for ``uvicorn doc_ray.entrypoints.mcp.server:app``.
app = create_app()
