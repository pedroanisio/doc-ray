"""MCP P1 — reach and honesty (BL-146): response_format, outline, entity profiles.

P0 (BL-145) made the server *teach* the retrieval model. P1 gives the agent the
reach to act on it and stops the entity surface from lying by omission:

* ``response_format="concise"`` strips per-row metadata the agent never cites,
  attacking the token ceiling that made a complete read look impossible.
* ``read_document_outline`` supplies the ordinals ``read_document_sentences``
  needs, so "read the ending" stops being a guess.
* ``entity_profiles`` folds the raw NER type-split into one row per entity with
  the disagreement measured and visible.

Plus the drift guards that hold *new* MCP surface to the same bar. Runs without a
network: ``httpx.AsyncClient`` is stubbed, as in test_mcp_corpus.py.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from doc_ray.entrypoints.mcp import compose, corpus, mutations, prompts, structure

_DOC = "11111111-1111-1111-1111-111111111111"
_TESTS = Path(__file__).resolve().parent
_REPO = _TESTS.parents[2]


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
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: client)
    return client


def _sentence(ordinal: int) -> dict:
    return {
        "id": f"01J{ordinal:023d}",
        "ordinal": ordinal,
        "text": f"sentence {ordinal}",
        "charStart": ordinal * 40,
        "charEnd": ordinal * 40 + 30,
        "cursor": f"cursor-{ordinal}",
    }


def _hit(ordinal: int) -> dict:
    return {
        "id": f"01J{ordinal:023d}",
        "documentId": _DOC,
        "title": "Dune",
        "ordinal": ordinal,
        "pre": "…",
        "hit": "spice",
        "post": "…",
        "cursor": f"cursor-{ordinal}",
    }


# --------------------------------------------------------------------------
# 1. response_format — the token lever, defaulting to today's shape.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detailed_is_the_default_so_existing_callers_are_unaffected(monkeypatch):
    client = _patch(monkeypatch, {"data": {"document": {"sentences": [_sentence(0)]}}})
    rows = await corpus.read_document_sentences(_DOC)
    assert rows == [_sentence(0)]
    assert "id" in client.calls[0][1]["query"]


@pytest.mark.asyncio
async def test_concise_reads_drop_metadata_but_never_the_citation_handle(monkeypatch):
    _patch(monkeypatch, {"data": {"document": {"sentences": [_sentence(0), _sentence(1)]}}})
    rows = await corpus.read_document_sentences(_DOC, response_format="concise")
    # Text and the offsets that make an excerpt citable always survive — the
    # saving must never come out of attribution.
    assert set(rows[0]) == {"ordinal", "text", "charStart", "charEnd"}
    assert rows[0]["text"] == "sentence 0"


@pytest.mark.asyncio
async def test_concise_keeps_the_cursor_on_the_last_row_so_paging_still_works(monkeypatch):
    _patch(monkeypatch, {"data": {"document": {"sentences": [_sentence(0), _sentence(1)]}}})
    rows = await corpus.read_document_sentences(_DOC, response_format="concise")
    assert "cursor" not in rows[0]
    assert rows[-1]["cursor"] == "cursor-1"  # only the last cursor is ever used


@pytest.mark.asyncio
async def test_concise_does_not_ask_the_api_for_fields_it_will_discard(monkeypatch):
    # The saving has to be on the wire too, not just in the tool's return value.
    client = _patch(monkeypatch, {"data": {"document": {"sentences": []}}})
    await corpus.read_document_sentences(_DOC, response_format="concise")
    selection = client.calls[0][1]["query"]
    assert "id " not in selection.split("{sentences")[1]


@pytest.mark.asyncio
async def test_concise_search_keeps_the_kwic_windows_and_the_coverage_signal(monkeypatch):
    _patch(monkeypatch, {"data": {"searchSentences": [_hit(5), _hit(9)]}})
    out = await corpus.search_sentences("spice", document_id=_DOC, response_format="concise")
    assert set(out) == {"hits", "coverage", "note"}  # BL-145 contract preserved
    assert set(out["hits"][0]) == {"ordinal", "pre", "hit", "post"}
    assert out["coverage"]["hit_count"] == 2


@pytest.mark.asyncio
async def test_concise_corpus_wide_search_keeps_the_document_id(monkeypatch):
    # Without a document filter the row is unattributable unless it says which
    # document it came from, so documentId is metadata that cannot be dropped.
    _patch(monkeypatch, {"data": {"searchSentences": [_hit(5)]}})
    out = await corpus.search_sentences("spice", response_format="concise")
    assert out["hits"][0]["documentId"] == _DOC


@pytest.mark.asyncio
async def test_an_unknown_response_format_is_rejected_locally_with_the_valid_values(monkeypatch):
    _patch(monkeypatch, {"data": {"document": {"sentences": []}}})
    with pytest.raises(corpus.ToolCallError) as ei:
        await corpus.read_document_sentences(_DOC, response_format="brief")
    assert ei.value.kind == "bad_input"
    assert "concise" in str(ei.value) and "detailed" in str(ei.value)


@pytest.mark.asyncio
async def test_the_concise_payload_is_measurably_smaller(monkeypatch):
    import json

    payload = {"data": {"document": {"sentences": [_sentence(i) for i in range(200)]}}}
    _patch(monkeypatch, payload)
    detailed = await corpus.read_document_sentences(_DOC)
    _patch(monkeypatch, payload)
    concise = await corpus.read_document_sentences(_DOC, response_format="concise")
    assert len(json.dumps(concise)) < len(json.dumps(detailed)) * 0.7


# --------------------------------------------------------------------------
# 2. read_document_outline — the map that aims the ranged reader.
# --------------------------------------------------------------------------


def _outline_payload(source: str, entries: list[dict], total: int = 4200) -> dict:
    return {
        "data": {
            "documentOutline": {
                "source": source,
                "totalSentences": total,
                "entryCount": len(entries),
                "note": None,
                "entries": entries,
            }
        }
    }


_ENTRY = {
    "level": 1,
    "title": "Book One",
    "ordinalStart": 0,
    "ordinalEnd": 1200,
    "charStart": 0,
    "charEnd": 48000,
    "pageStart": 1,
    "pageEnd": 60,
}


@pytest.mark.asyncio
async def test_the_outline_returns_ordinal_ranges_ready_for_the_ranged_reader(monkeypatch):
    _patch(monkeypatch, _outline_payload("headings", [_ENTRY]))
    out = await structure.read_document_outline(_DOC)
    assert out["source"] == "headings"
    entry = out["entries"][0]
    # The whole point: these two numbers feed straight into read_document_sentences.
    assert (entry["ordinalStart"], entry["ordinalEnd"]) == (0, 1200)
    assert entry["title"] == "Book One"


@pytest.mark.asyncio
async def test_the_outline_declares_where_its_structure_came_from(monkeypatch):
    # A uniform grid and a real table of contents are not the same claim, and the
    # agent must be able to tell them apart without reading the note.
    _patch(monkeypatch, _outline_payload("uniform", [_ENTRY]))
    out = await structure.read_document_outline(_DOC)
    assert out["source"] == "uniform"


@pytest.mark.asyncio
async def test_a_document_with_no_detected_headings_still_returns_a_usable_map(monkeypatch):
    # Dune Messiah has zero detected headings. Returning [] would fail exactly the
    # document the tool exists for.
    grid = [dict(_ENTRY, title="Sentences 0–209", ordinalStart=0, ordinalEnd=209)]
    _patch(monkeypatch, _outline_payload("uniform", grid))
    out = await structure.read_document_outline(_DOC)
    assert out["entries"]
    assert out["source"] == "uniform"


@pytest.mark.asyncio
async def test_the_outline_validates_the_document_id_before_the_network(monkeypatch):
    client = _patch(monkeypatch, _outline_payload("headings", []))
    with pytest.raises(corpus.ToolCallError) as ei:
        await structure.read_document_outline("not-a-uuid")
    assert ei.value.kind == "bad_input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_an_unreadable_document_yields_an_empty_outline_not_an_exception(monkeypatch):
    _patch(monkeypatch, {"data": {"documentOutline": None}})
    out = await structure.read_document_outline(_DOC)
    assert out["entries"] == []
    assert out["source"] == "empty"


# --------------------------------------------------------------------------
# 3. entity_profiles — reconciled identities with the disagreement visible.
# --------------------------------------------------------------------------


_PROFILE = {
    "surface": "Arrakis",
    "normalizedForm": "arrakis",
    "mentions": 546,
    "dominantType": "LOC",
    "typeSpread": [
        {"entityType": "LOC", "count": 225},
        {"entityType": "GPE", "count": 150},
        {"entityType": "PERSON", "count": 95},
        {"entityType": "NORP", "count": 76},
    ],
    "typeConsistency": 0.4121,
    "surfaceForms": ["Arrakis"],
    "canonicalId": None,
    "canonicalName": None,
    "wikidataId": None,
    "resolved": False,
}


@pytest.mark.asyncio
async def test_entity_profiles_return_one_row_per_entity_with_the_type_spread(monkeypatch):
    _patch(monkeypatch, {"data": {"entityProfiles": [_PROFILE]}})
    (profile,) = await structure.entity_profiles(document_id=_DOC)
    assert profile["mentions"] == 546
    assert profile["dominantType"] == "LOC"
    assert len(profile["typeSpread"]) == 4
    assert profile["typeConsistency"] < 0.5  # the "do not trust this type" flag


@pytest.mark.asyncio
async def test_entity_profiles_say_whether_the_identity_is_registry_backed(monkeypatch):
    # PALS's Law: an unresolved row must not look like a canonical identity.
    _patch(monkeypatch, {"data": {"entityProfiles": [_PROFILE]}})
    (profile,) = await structure.entity_profiles()
    assert profile["resolved"] is False
    assert profile["canonicalId"] is None


@pytest.mark.asyncio
async def test_entity_profiles_validate_the_document_id_before_the_network(monkeypatch):
    client = _patch(monkeypatch, {"data": {"entityProfiles": []}})
    with pytest.raises(corpus.ToolCallError):
        await structure.entity_profiles(document_id="nope")
    assert client.calls == []


# --------------------------------------------------------------------------
# 4. Drift protection — new MCP surface inherits every guard, mechanically.
# --------------------------------------------------------------------------


_ALL_TOOLS = (*corpus.TOOLS, *structure.TOOLS, *compose.TOOLS, *mutations.TOOLS)


def test_every_tool_is_exercised_by_the_graphql_schema_contract_guard():
    # test_mcp_schema_contract.py validates each tool's GraphQL document against
    # the live schema — but from a hand-maintained call list, so a new tool is
    # silently uncovered. This closes that hole: adding a tool without adding it
    # there fails the build.
    source = (_TESTS / "test_mcp_schema_contract.py").read_text()
    exercised = set(re.findall(r"(?:corpus|structure|compose|mutations)\.(\w+)\(", source))
    # Only tools that embed a GraphQL document need validating. The composition
    # tools delegate to the read tools and hold no query string of their own, so
    # their documents are already covered by the tool they call.
    missing = sorted(
        tool.__name__
        for tool in _ALL_TOOLS
        if "gql(" in inspect.getsource(tool) and tool.__name__ not in exercised
    )
    assert not missing, (
        "these MCP tools send GraphQL nobody validates against the schema — add them "
        f"to _READ_CALLS/_WRITE_CALLS in test_mcp_schema_contract.py: {missing}"
    )


def test_every_read_tool_is_documented_in_the_tool_inventory():
    # The whole BL-145/BL-146 thesis is that undocumented surface gets misused.
    # An inventory that silently falls behind the code is the same failure.
    manual = (_REPO / "docs" / "mcp-service.md").read_text()
    undocumented = sorted(
        tool.__name__
        for tool in (*corpus.TOOLS, *structure.TOOLS, *compose.TOOLS)
        if tool.__name__ not in manual
    )
    assert not undocumented, f"missing from docs/mcp-service.md: {undocumented}"


def test_every_prompt_is_documented_in_the_inventory():
    # Prompts are the user-controlled primitive (Item 8): an undocumented workflow
    # is one a user cannot discover the shape of. Same guard as the tools.
    manual = (_REPO / "docs" / "mcp-service.md").read_text()
    undocumented = sorted(p.__name__ for p in prompts.PROMPTS if p.__name__ not in manual)
    assert not undocumented, f"prompts missing from docs/mcp-service.md: {undocumented}"


def test_every_tool_offering_response_format_validates_it():
    # A silently-ignored format argument is worse than none: the agent believes it
    # bought a saving it never got.
    offenders = []
    for tool in _ALL_TOOLS:
        params = inspect.signature(tool).parameters
        if "response_format" in params and "_require_response_format" not in inspect.getsource(
            tool
        ):
            offenders.append(tool.__name__)
    assert not offenders, f"response_format must be validated: {offenders}"


def test_the_readers_that_return_bulk_text_offer_the_concise_format():
    # These two dominate an agent's token budget; if a future bulk reader lands
    # without the lever, this points at it.
    for tool in (corpus.read_document_sentences, corpus.search_sentences):
        assert "response_format" in inspect.signature(tool).parameters, tool.__name__
