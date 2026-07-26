"""Semantic briefing retrieval across the MCP + GraphQL surfaces (#33), no network.

The shared service's ranking is covered in test_briefing_service.py. Here we prove
the two driving adapters: each reader translates "stack absent" into the shared
``SemanticUnavailable`` signal and normalizes its rows to the same shape — so the
MCP ``briefing_pack`` and GraphQL ``briefingPack`` stay **byte-equivalent** for the
same visible corpus slice (an explicit acceptance criterion).
"""

from __future__ import annotations

import json

import pytest

from doc_ray.application.briefing import SemanticUnavailable, build_briefing_pack
from doc_ray.entrypoints.api.briefing_query import _GraphQLBriefingReader
from doc_ray.entrypoints.mcp import compose, corpus, semantic


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


# --------------------------------------------------------------------------
# MCP reader: translation + normalization.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_reader_maps_unsupported_to_semantic_unavailable(monkeypatch):
    async def _unsupported(query, document_id=None, limit=25):
        raise corpus.ToolCallError("unsupported", "sentence stack not installed")

    monkeypatch.setattr(semantic, "semantic_search_sentences", _unsupported)
    with pytest.raises(SemanticUnavailable):
        await compose._MCP_READER.semantic_search_sentences("t", document_id="d1", limit=4)


@pytest.mark.asyncio
async def test_mcp_reader_normalizes_semantic_rows(monkeypatch):
    async def _hits(query, document_id=None, limit=25):
        return [
            {
                "sentenceId": "s2",
                "documentId": "d1",
                "ordinal": 2,
                "text": "para",
                "similarity": 0.9,
            }
        ]

    monkeypatch.setattr(semantic, "semantic_search_sentences", _hits)
    rows = await compose._MCP_READER.semantic_search_sentences("t", document_id="d1", limit=4)
    assert rows == [{"id": "s2", "documentId": "d1", "ordinal": 2, "text": "para"}]


@pytest.mark.asyncio
async def test_mcp_reader_reraises_non_unsupported_toolcall_errors(monkeypatch):
    async def _boom(query, document_id=None, limit=25):
        raise corpus.ToolCallError("upstream_error", "boom")

    monkeypatch.setattr(semantic, "semantic_search_sentences", _boom)
    with pytest.raises(corpus.ToolCallError):  # only `unsupported` becomes a fallback
        await compose._MCP_READER.semantic_search_sentences("t", document_id="d1", limit=4)


# --------------------------------------------------------------------------
# GraphQL reader: translation + normalization.
# --------------------------------------------------------------------------


class _FakeEncoder:
    model_id = "test-encoder-v1"

    def embed_texts(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.mark.asyncio
async def test_graphql_reader_maps_missing_stack_to_semantic_unavailable(monkeypatch):
    from doc_ray.adapters.text_encoder_sentence_transformers import SemanticStackUnavailableError
    from doc_ray.entrypoints.api import semantic_query

    def _raise():
        raise SemanticStackUnavailableError("sentence_transformers is not installed")

    monkeypatch.setattr(semantic_query, "_query_text_encoder", _raise)
    with pytest.raises(SemanticUnavailable):
        await _GraphQLBriefingReader(object()).semantic_search_sentences("t", limit=4)


@pytest.mark.asyncio
async def test_graphql_reader_normalizes_semantic_rows(monkeypatch):
    from doc_ray.adapters import persistence_semantic_read as semantic_read
    from doc_ray.entrypoints.api import semantic_query

    async def _read(conn, vector, model, *, document_id=None, limit=25):
        assert model == "test-encoder-v1"
        return [{"sentence_id": "s2", "document_id": "d1", "ordinal": 2, "text": "para"}]

    monkeypatch.setattr(semantic_query, "_query_text_encoder", lambda: _FakeEncoder())
    monkeypatch.setattr(semantic_read, "semantic_search_sentences", _read)
    rows = await _GraphQLBriefingReader(object()).semantic_search_sentences("t", limit=4)
    assert rows == [{"id": "s2", "documentId": "d1", "ordinal": 2, "text": "para"}]


# --------------------------------------------------------------------------
# Byte-equivalence: MCP briefing_pack == GraphQL briefingPack for one slice.
# --------------------------------------------------------------------------

_DOC = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_SEM_HIT_WIRE = {  # GraphQL wire shape (camelCase) the MCP reader receives
    "sentenceId": "s7",
    "documentId": _DOC,
    "ordinal": 7,
    "text": "The mill weaves algebra as the loom weaves flowers.",
    "similarity": 0.88,
}
_SEM_HIT_ROW = {  # persistence shape the GraphQL reader receives
    "sentence_id": "s7",
    "document_id": _DOC,
    "ordinal": 7,
    "text": "The mill weaves algebra as the loom weaves flowers.",
}


def _mcp_router(monkeypatch):
    def handler(query, variables):
        if "fullTextSearch" in query:
            return {"fullTextSearch": [{"documentId": _DOC, "title": "Notes", "rank": 0.9}]}
        if "semanticSearchSentences" in query:
            return {"semanticSearchSentences": [_SEM_HIT_WIRE]}
        if "entityFrequency" in query:
            return {"entityFrequency": [{"text": "Ada", "entityType": "PERSON", "count": 3}]}
        if "searchSentences" in query:
            return {"searchSentences": {"hits": [], "coverage": {}, "note": None}}
        raise AssertionError(f"unexpected query: {query}")

    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._p

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers):
            return _Resp({"data": handler(json["query"], json["variables"])})

    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: _Client())


def _graphql_reader(monkeypatch) -> _GraphQLBriefingReader:
    from doc_ray.adapters import persistence_read as read
    from doc_ray.adapters import persistence_semantic_read as semantic_read
    from doc_ray.entrypoints.api import semantic_query

    async def _fts(conn, topic, *, limit):
        return [{"doc_id": _DOC, "title": "Notes", "rank": 0.9}]

    async def _sem(conn, vector, model, *, document_id=None, limit=25):
        return [_SEM_HIT_ROW]

    async def _ents(conn, *, document_id, limit):
        return [{"text": "Ada", "entity_type": "PERSON", "count": 3}]

    monkeypatch.setattr(read, "fulltext_search", _fts)
    monkeypatch.setattr(read, "entity_frequency", _ents)
    monkeypatch.setattr(semantic_read, "semantic_search_sentences", _sem)
    monkeypatch.setattr(semantic_query, "_query_text_encoder", lambda: _FakeEncoder())
    return _GraphQLBriefingReader(object())


@pytest.mark.asyncio
async def test_mcp_and_graphql_briefings_are_byte_equivalent(monkeypatch):
    _mcp_router(monkeypatch)
    mcp_pack = await build_briefing_pack(
        "analytical engine", reader=compose._MCP_READER, max_documents=2, passages_per_document=4
    )
    gql_pack = await build_briefing_pack(
        "analytical engine",
        reader=_graphql_reader(monkeypatch),
        max_documents=2,
        passages_per_document=4,
    )
    assert json.dumps(mcp_pack, sort_keys=True) == json.dumps(gql_pack, sort_keys=True)
    # and it genuinely ran the semantic path (not a lexical fallback)
    assert "semantic" in mcp_pack["retrieval"].lower()
    assert mcp_pack["key_passages"][0]["sentence_ordinal"] == 7
    assert mcp_pack["key_passages"][0]["matched_terms"] == []
