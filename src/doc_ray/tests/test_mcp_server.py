"""The MCP server registers the corpus tools and fronts them with bearer auth."""

from __future__ import annotations

import logging

import anyio
from starlette.applications import Starlette

from doc_ray.entrypoints.mcp import corpus, server
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
        "cross_modal_image_search",
        "visual_captions",
        # composition-grounding tools
        "briefing_pack",
        "entity_dossier",
        "document_evidence",
    }


def test_registered_tools_keep_their_input_schemas():
    # The audit wrapper must not disturb FastMCP's signature-derived schemas.
    tools = {tool.name: tool for tool in anyio.run(server.build_server().list_tools)}
    assert "term" in tools["search_sentences"].inputSchema["properties"]
    assert "after" in tools["search_sentences"].inputSchema["properties"]
    assert "topic" in tools["briefing_pack"].inputSchema["properties"]
    assert tools["get_document"].description  # docstrings survive as descriptions


def test_tool_calls_emit_an_audit_log_line(monkeypatch, caplog):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"fullTextSearch": []}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers):
            return _Resp()

    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: _Client())
    mcp = server.build_server()
    with caplog.at_level(logging.INFO, logger="doc_ray.mcp.audit"):
        anyio.run(mcp.call_tool, "full_text_search", {"query": "x"})
    assert any("tool=full_text_search" in r.getMessage() for r in caplog.records)


def test_create_app_is_streamable_http_with_bearer_auth():
    app = server.create_app()
    assert isinstance(app, Starlette)
    assert any(mw.cls is BearerAuthMiddleware for mw in app.user_middleware)


def test_module_exposes_an_asgi_app():
    assert isinstance(server.app, Starlette)
