"""An empty semantic ranking is a precondition failure, not a finding.

``semantic_search_sentences`` ranks by cosine distance with **no similarity
floor**: every vector in scope is a candidate, and the ``LIMIT`` cuts the tail.
Zero rows therefore cannot mean "nothing was similar enough" — it can only mean
there was nothing to rank. Embedding is opt-in per document
(``embed_document_sentences`` is a worker job, never auto-chained after ingest),
so the common cause is a document nobody has embedded.

Returning ``[]`` made those two readings indistinguishable, and the wrong one is
the one a caller acts on: a reviewer recorded "no semantically similar sentences
in this book" for a book that simply had no vectors. The contrast with
``search_sentences`` — which states in prose that its zero was an exhaustive scan
— is what made the silence indefensible.

Sibling to ``test_search_coverage.py``: same principle (absence must be
qualified), other retrieval path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

# Import the server eagerly so the `mcp` SDK loads (with the real httpx) before
# any test patches httpx.AsyncClient.
from doc_ray.entrypoints.mcp import compose, corpus, semantic, server  # noqa: F401

_DOC = "11111111-1111-1111-1111-111111111111"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        return _FakeResp(self.payload)


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


def _patch(monkeypatch, payload):
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=_FakeClient(payload)))


def _empty(monkeypatch):
    _patch(monkeypatch, {"data": {"semanticSearchSentences": []}})


@pytest.mark.asyncio
async def test_an_empty_ranking_fails_typed_instead_of_returning_no_rows(monkeypatch):
    _empty(monkeypatch)

    with pytest.raises(corpus.ToolCallError) as excinfo:
        await semantic.semantic_search_sentences("dashboard density", document_id=_DOC)

    assert excinfo.value.kind == "not_found"


@pytest.mark.asyncio
async def test_the_message_refuses_the_reading_that_nothing_was_similar(monkeypatch):
    _empty(monkeypatch)

    with pytest.raises(corpus.ToolCallError) as excinfo:
        await semantic.semantic_search_sentences("dashboard density", document_id=_DOC)

    message = str(excinfo.value).lower()
    assert "no similarity floor" in message  # why the empty set cannot mean "not similar"
    # Why it is empty, in terms the caller can act on. Embedding is no longer
    # opt-in — it is chained into every ingest — so an absent document now means
    # its chained job is unfinished or failed, not that nobody asked for it.
    assert "chained embed job" in message


@pytest.mark.asyncio
async def test_the_hint_names_the_tool_that_fixes_it_and_the_lexical_fallback(monkeypatch):
    _empty(monkeypatch)

    with pytest.raises(corpus.ToolCallError) as excinfo:
        await semantic.semantic_search_sentences("dashboard density", document_id=_DOC)

    hint = excinfo.value.hint or ""
    assert "embed_document_sentences" in hint
    assert "search_sentences" in hint


@pytest.mark.asyncio
async def test_a_document_scoped_failure_names_the_document(monkeypatch):
    # The remedy is per-document, so the message has to carry the id the caller
    # would pass to embed_document_sentences.
    _empty(monkeypatch)

    with pytest.raises(corpus.ToolCallError) as excinfo:
        await semantic.semantic_search_sentences("dashboard density", document_id=_DOC)

    assert _DOC in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_corpus_wide_failure_bounds_its_claim_to_the_visible_corpus(monkeypatch):
    # RLS scopes every read; "the corpus" always means this caller's corpus.
    _empty(monkeypatch)

    with pytest.raises(corpus.ToolCallError) as excinfo:
        await semantic.semantic_search_sentences("dashboard density")

    assert "visible to you" in str(excinfo.value)


@pytest.mark.asyncio
async def test_hits_are_returned_unchanged_when_the_scope_is_embedded(monkeypatch):
    row = {
        "sentenceId": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "documentId": _DOC,
        "ordinal": 312,
        "text": "Choice overload slows the decision.",
        "similarity": 0.81,
    }
    _patch(monkeypatch, {"data": {"semanticSearchSentences": [row]}})

    assert await semantic.semantic_search_sentences("choice", document_id=_DOC) == [row]


@pytest.mark.asyncio
async def test_the_briefing_port_still_sees_an_empty_page_not_a_failure(monkeypatch):
    # The typed error is for the AGENT, who reads `[]` as a finding about the
    # corpus. The BriefingReader port reads it as state: `_probe_semantic`
    # distinguishes no_vectors from stack-unavailable and reports the sharper
    # gap ("No sentence vectors are visible in your corpus yet"). Collapsing
    # both into SemanticUnavailable would downgrade that message, so the port
    # keeps its contract while the tool boundary keeps its honesty.
    _empty(monkeypatch)

    rows = await compose._MCP_READER.semantic_search_sentences("x", document_id=_DOC, limit=5)

    assert rows == []
