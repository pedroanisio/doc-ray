"""The MCP server registers the corpus tools and fronts them with bearer auth."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import anyio
import pytest
from starlette.applications import Starlette

from doc_ray.entrypoints.mcp import corpus, server
from doc_ray.entrypoints.mcp.auth import BearerAuthMiddleware

_READ_TOOL_NAMES = {
    # corpus read tools
    "search_sentences",
    "read_document_sentences",
    "full_text_search",
    "list_documents",
    "get_document",
    "get_job",
    "list_collections",
    "get_collection",
    # Lens Registry (Block B): the scopes read side
    "list_lenses",
    "corpus_manifest",
    "corpus_shard",
    "entity_frequency",
    "lexeme_kwic",
    # semantic retrieval (NLP Layer 20): meaning-based sentence search
    "semantic_search_sentences",
    # structure tools (BL-146): the map, and reconciled entities
    "read_document_outline",
    "entity_profiles",
    # materialized mention-confidence review queue (Block G)
    "uncertain_mentions",
    # term-frequency pre-flight (Item 10): flood-risk before searching
    "term_frequency",
    # Prism visual tools
    "document_images",
    "image_class_summary",
    "cross_modal_image_search",
    "visual_captions",
    "visual_review",
    # NER-correction audit log (Block A)
    "entity_mention_corrections",
    "document_classification",
    # lens-scoped entity identity (Block D)
    "entity_projection",
    "lens_entities",
    # hyper-context relationship discovery (Block F)
    "entity_relations",
    "related_entities",
    # cross-lens allusion edges (Block E)
    "allusions",
    # composition-grounding tools
    "briefing_pack",
    "entity_dossier",
    "document_evidence",
}

#: MCP's user-controlled primitive (Item 8): the corpus methodology as workflows.
#: Exact set — adding a prompt without registering it here fails the build.
_PROMPT_NAMES = {
    "summarize_document",
    "ground_a_claim",
    "survey_entities",
    "sweep_the_corpus",
}

_WRITE_TOOL_NAMES = {
    "ingest_document",
    "reannotate",
    "reextract_document",
    "annotate_document",
    "semantic_enrich_document",
    "embed_document_sentences",
    "canonicalize_document",
    "suggest_seeds",
    "suggest_document_metadata",
    "update_document_metadata",
    "bulk_tag_documents",
    "correct_entity_mention",
    "correct_entity_surface",
    "classify_document",
    "confirm_document_classification",
    "decide_seeds",
    "decide_document_metadata_suggestions",
    "decide_visual_captions",
    "seed_approved",
    "create_collection",
    "add_document_to_collection",
    "remove_document_from_collection",
    "update_collection",
    "delete_collection",
    # Lens Registry (Block B): the scope-curation write side
    "promote_collection_to_lens",
    "demote_lens",
    "set_document_default_lens",
    # Projection engine (Block D): human review of lens-scoped identity
    "confirm_projection",
    "reject_projection",
    # Allusion edges (Block E): typed cross-lens curation
    "propose_allusion",
    "confirm_allusion",
    "reject_allusion",
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


def test_build_server_uses_restart_safe_stateless_streamable_http():
    """Unary tools must not bind clients to one process-local session id."""
    mcp = server.build_server()

    assert mcp.settings.stateless_http is True
    mcp.streamable_http_app()
    manager = mcp.session_manager
    assert manager.stateless is True
    assert manager.event_store is None
    assert manager._server_instances == {}


@pytest.mark.asyncio
async def test_streamable_http_dispatches_every_request_without_session_lookup(monkeypatch):
    """The SDK manager takes its stateless path, never the process-local session map."""
    mcp = server.build_server()
    mcp.streamable_http_app()
    manager = mcp.session_manager
    manager._task_group = object()
    stateless = AsyncMock()
    stateful = AsyncMock()
    monkeypatch.setattr(manager, "_handle_stateless_request", stateless)
    monkeypatch.setattr(manager, "_handle_stateful_request", stateful)

    receive = AsyncMock()
    send = AsyncMock()
    await manager.handle_request({"type": "http"}, receive, send)

    stateless.assert_awaited_once_with({"type": "http"}, receive, send)
    stateful.assert_not_awaited()


def test_build_server_registers_exactly_the_four_prompts_each_described():
    """MCP's user-controlled primitive: the four workflows, each with a description."""
    mcp = server.build_server()
    prompts = anyio.run(mcp.list_prompts)
    assert {p.name for p in prompts} == _PROMPT_NAMES
    assert all((p.description or "").strip() for p in prompts)


def test_prompts_are_registered_regardless_of_the_write_flag():
    """Prompts are read-only workflows: present with or without write tools enabled."""
    for enable in (False, True):
        mcp = server.build_server(enable_write=enable)
        assert {p.name for p in anyio.run(mcp.list_prompts)} == _PROMPT_NAMES


def test_the_audit_wrapper_preserves_prompt_argument_schemas():
    # The audit wrapper must not disturb FastMCP's signature-derived prompt
    # arguments (it wraps prompts too), so the client still sees them.
    prompts = {p.name: p for p in anyio.run(server.build_server().list_prompts)}
    summarize_args = {a.name for a in (prompts["summarize_document"].arguments or [])}
    assert "document_id" in summarize_args
    sweep_args = {a.name for a in (prompts["sweep_the_corpus"].arguments or [])}
    assert "shard_count" in sweep_args


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

    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=_Client()))
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
