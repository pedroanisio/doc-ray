"""MCP P0 self-teaching improvements (BL-145): server instructions, search coverage,
cursor steering, and a drift guard that holds new MCP surface to the same bar.

Grounded in a user's Dune-summary write-up and Anthropic's *Writing effective tools
for AI agents*: the friction was the agent re-learning the retrieval model, so the
fix is to make the server teach it — through instructions, a coverage signal that
turns a flooded search into a legible diagnosis, and error messages that steer.

Runs without a network: ``httpx.AsyncClient`` is stubbed, like test_mcp_corpus.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_ray.entrypoints.mcp import corpus, server


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
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        self.calls.append((url, json, headers))
        return _FakeResp(self.payload)


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


def _patch(monkeypatch, payload):
    client = _FakeClient(payload)
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=client))
    return client


# --------------------------------------------------------------------------
# 1. Server instructions — teach the retrieval model once, up front.
# --------------------------------------------------------------------------


def test_server_exposes_instructions_that_teach_the_retrieval_model():
    text = server.SERVER_INSTRUCTIONS
    assert isinstance(text, str) and len(text) > 200
    low = text.lower()
    # It must name the model and the recommended path, not just list tools.
    assert "retrieval" in low
    assert "rare" in low or "specific" in low  # the anti-flooding lesson
    assert "ordinal" in low or "range" in low  # the ranged reader
    assert "read_document_sentences" in text and "search_sentences" in text


def test_build_server_passes_instructions_to_fastmcp():
    mcp = server.build_server()
    # FastMCP stores the constructor instructions; whichever attribute it exposes,
    # the server's advertised guidance must be the same non-empty text.
    advertised = getattr(mcp, "instructions", None) or getattr(
        getattr(mcp, "_mcp_server", None), "instructions", None
    )
    assert advertised == server.SERVER_INSTRUCTIONS


# --------------------------------------------------------------------------
# 2. search_sentences returns a coverage signal, and steers on flooding.
# --------------------------------------------------------------------------


def _hit(ordinal, i=0):
    return {"id": str(i), "documentId": "d1", "ordinal": ordinal, "pre": "", "hit": "x", "post": ""}


@pytest.mark.asyncio
async def test_search_returns_hits_plus_coverage(monkeypatch):
    _patch(monkeypatch, {"data": {"searchSentences": [_hit(12), _hit(880), _hit(1904)]}})
    out = await corpus.search_sentences("kanly", limit=20)
    assert set(out) == {"hits", "coverage", "note"}
    assert len(out["hits"]) == 3
    cov = out["coverage"]
    assert cov["hit_count"] == 3
    assert cov["ordinal_min"] == 12 and cov["ordinal_max"] == 1904
    assert cov["limit_reached"] is False
    assert out["note"] is None  # nothing to steer: under the limit


@pytest.mark.asyncio
async def test_search_steers_when_the_limit_is_hit(monkeypatch):
    # A flood signature: the cap is reached and the hits cluster low in the document.
    _patch(monkeypatch, {"data": {"searchSentences": [_hit(o) for o in (5, 40, 120)]}})
    out = await corpus.search_sentences("Paul", limit=3)
    assert out["coverage"]["limit_reached"] is True
    note = out["note"]
    assert note and "read_document_sentences" in note
    assert "rarer" in note.lower() or "specific" in note.lower()
    assert "ordinal" in note.lower()


@pytest.mark.asyncio
async def test_search_coverage_survives_missing_ordinals(monkeypatch):
    _patch(monkeypatch, {"data": {"searchSentences": [{"id": "1", "hit": "x"}]}})
    out = await corpus.search_sentences("term", limit=5)
    assert out["coverage"]["hit_count"] == 1
    assert out["coverage"]["ordinal_min"] is None


# --------------------------------------------------------------------------
# 3. Cursor non-interchangeability is steered, not a bare rejection.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_cursor_error_is_steered_with_an_actionable_hint(monkeypatch):
    # The API rejects a search cursor handed to the document reader; the tool must
    # translate that into guidance rather than re-raising an opaque failure.
    _patch(monkeypatch, {"errors": [{"message": "invalid cursor for documents connection"}]})
    with pytest.raises(corpus.ToolCallError) as ei:
        await corpus.read_document_sentences("11111111-1111-1111-1111-111111111111")
    err = ei.value
    assert err.kind == "bad_input"
    assert "interchangeable" in str(err).lower()
    assert "from_ordinal" in str(err) or "ordinal" in str(err).lower()


def test_cursor_messages_classify_as_bad_input():
    assert corpus._classify_graphql_error("invalid cursor") == "bad_input"


# --------------------------------------------------------------------------
# 3b. A statement timeout is a scope problem, and must be typed as one.
# --------------------------------------------------------------------------


def test_a_statement_timeout_classifies_as_timeout_not_a_generic_upstream_error():
    # Postgres' own wording, produced by the API pool's statement_timeout. As an
    # unclassified upstream_error it read as "the server is broken", so a caller
    # abandoned a capability that works fine one scope narrower.
    message = "canceling statement due to statement timeout"
    assert corpus._classify_graphql_error(message) == "timeout"


@pytest.mark.asyncio
async def test_a_timed_out_read_is_told_how_to_narrow_itself(monkeypatch):
    _patch(monkeypatch, {"errors": [{"message": "canceling statement due to statement timeout"}]})
    with pytest.raises(corpus.ToolCallError) as ei:
        await corpus.search_sentences("the")
    err = ei.value
    assert err.kind == "timeout"
    assert err.retriable is False  # repeating it unchanged times out again
    hint = err.hint or ""
    assert "document_id" in hint and "limit" in hint


# --------------------------------------------------------------------------
# 4. Drift guard — new MCP surface gets the same treatment.
# --------------------------------------------------------------------------


def test_every_read_tool_has_a_substantive_description():
    # Anthropic: descriptions are load-bearing. A tool with a thin docstring is a
    # tool the agent will misuse — so every registered tool must teach its use.
    thin = []
    for tool in (
        *corpus.TOOLS,
        *__import__("doc_ray.entrypoints.mcp.compose", fromlist=["TOOLS"]).TOOLS,
    ):
        doc = (tool.__doc__ or "").strip()
        if len(doc) < 80:
            thin.append(f"{tool.__name__} ({len(doc)} chars)")
    assert not thin, f"MCP tools need a substantive description (>=80 chars): {thin}"


def test_search_sentences_states_its_retrieval_model_in_its_own_description():
    # A reviewer read `Hick` -> "thick", "thickness" and concluded the tool had
    # lemma-normalized a proper noun — then inferred that corpus-wide and
    # document-scoped search used two different matching rules. Both readings are
    # wrong (it is one ILIKE substring scan either way), and both were available
    # only because the description never said what it matched. The fact was
    # documented in domain/term_frequency.py, where the caller never looks.
    doc = corpus.search_sentences.__doc__ or ""
    lowered = doc.lower()
    assert "substring" in lowered
    assert "identical" in lowered or "same" in lowered  # scoped == corpus-wide
    assert "term_frequency" in doc  # the pre-flight, named where it is needed


def test_read_tools_validate_document_ids_before_the_network():
    # Every tool taking a document_id must reject a malformed id locally (typed
    # bad_input), never spend a round-trip on it — the guard that keeps errors
    # actionable as the surface grows.
    import inspect

    offenders = []
    for tool in corpus.TOOLS:
        params = inspect.signature(tool).parameters
        if "document_id" in params:
            src = inspect.getsource(tool)
            if "_require_uuid" not in src:
                offenders.append(tool.__name__)
    assert not offenders, f"document_id tools must call _require_uuid: {offenders}"
