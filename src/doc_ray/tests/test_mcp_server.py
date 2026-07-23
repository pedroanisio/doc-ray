"""The MCP server registers the corpus tools and fronts them with bearer auth."""

from __future__ import annotations

import logging

import anyio
from starlette.applications import Starlette

from doc_ray.entrypoints.mcp import corpus, server
from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware

_READ_TOOL_NAMES = {
    # corpus read tools
    "search_sentences",
    "full_text_search",
    "list_documents",
    "get_document",
    "get_job",
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

_WRITE_TOOL_NAMES = {
    "reannotate",
    "reextract_document",
    "annotate_document",
    "semantic_enrich_document",
    "canonicalize_document",
    "suggest_seeds",
    "suggest_document_metadata",
    "update_document_metadata",
    "bulk_tag_documents",
    "decide_seeds",
    "decide_document_metadata_suggestions",
    "seed_approved",
}


def test_build_server_registers_only_read_tools_by_default():
    """Default posture is read-only: no write tool is even advertised in tools/list."""
    mcp = server.build_server()
    names = {tool.name for tool in anyio.run(mcp.list_tools)}
    assert names == _READ_TOOL_NAMES
    assert not (names & _WRITE_TOOL_NAMES)


def test_build_server_adds_write_tools_when_enabled():
    mcp = server.build_server(enable_write=True)
    names = {tool.name for tool in anyio.run(mcp.list_tools)}
    assert names == _READ_TOOL_NAMES | _WRITE_TOOL_NAMES


def test_create_app_threads_config_flag_into_registration(monkeypatch):
    """create_app passes cfg.mcp_enable_write straight to build_server."""
    import dataclasses

    from doc_ray.config import Config

    seen = {}
    real_build = server.build_server

    def _fake_build(enable_write=False):
        seen["enable_write"] = enable_write
        return real_build(enable_write=enable_write)

    monkeypatch.setattr(server, "build_server", _fake_build)
    cfg = dataclasses.replace(Config.from_env(), mcp_enable_write=True)
    server.create_app(cfg)
    assert seen["enable_write"] is True


def test_config_default_keeps_mcp_read_only():
    from doc_ray.config import Config

    assert Config.from_env().mcp_enable_write is False


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
