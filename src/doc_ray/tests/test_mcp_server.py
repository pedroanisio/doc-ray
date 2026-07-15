"""The MCP server registers the corpus tools and fronts them with bearer auth."""

from __future__ import annotations

import anyio
from starlette.applications import Starlette

from doc_ray.entrypoints.mcp import server
from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware


def test_build_server_registers_the_read_and_composition_tools():
    mcp = server.build_server()
    names = {tool.name for tool in anyio.run(mcp.list_tools)}
    assert names == {
        # corpus read tools
        "search_sentences",
        "full_text_search",
        "list_documents",
        "get_document",
        "entity_frequency",
        "lexeme_kwic",
        # Prism visual tools
        "document_images",
        "image_class_summary",
        # composition-grounding tools
        "briefing_pack",
        "entity_dossier",
        "document_evidence",
    }


def test_create_app_is_streamable_http_with_bearer_auth():
    app = server.create_app()
    assert isinstance(app, Starlette)
    assert any(mw.cls is BearerAuthMiddleware for mw in app.user_middleware)


def test_module_exposes_an_asgi_app():
    assert isinstance(server.app, Starlette)
