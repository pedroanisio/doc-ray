"""MCP composition-grounding tools assemble cited evidence over the read tools (no network)."""

from __future__ import annotations

import pytest

from doc_ray.entrypoints.mcp import compose, corpus


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Router:
    """Query-aware fake httpx client: routes each GraphQL op to canned data."""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []
        self.headers = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        self.calls.append(json)
        self.headers.append(headers)
        return _FakeResp({"data": self._handler(json["query"], json["variables"])})


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


def _install(monkeypatch, handler):
    router = _Router(handler)
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: router)
    return router


# --- briefing_pack ----------------------------------------------------------

_DOCS = [
    {
        "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "title": "The Field Equations (1915)",
        "rank": 0.9,
    },
    {
        "documentId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "title": "Schwarzschild Solution",
        "rank": 0.7,
    },
]
_SENTS = {
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": [
        {
            "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "title": "The Field Equations (1915)",
            "ordinal": 12,
            "pre": "In ",
            "hit": "general relativity",
            "post": " gravity is curvature.",
        },
        {
            "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "title": "The Field Equations (1915)",
            "ordinal": 40,
            "pre": "",
            "hit": "General relativity",
            "post": " predicts light bending.",
        },
    ],
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": [
        {
            "documentId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "title": "Schwarzschild Solution",
            "ordinal": 3,
            "pre": "The ",
            "hit": "general relativity",
            "post": " metric is exact.",
        },
    ],
}
_ENTS = {
    "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": [
        {"text": "Albert Einstein", "entityType": "PERSON", "count": 5},
        {"text": "Mercury", "entityType": "GPE", "count": 2},
    ],
    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": [
        {"text": "Albert Einstein", "entityType": "PERSON", "count": 3},
        {"text": "Karl Schwarzschild", "entityType": "PERSON", "count": 4},
    ],
}


def _briefing_handler(query, variables):
    if "fullTextSearch" in query:
        return {"fullTextSearch": _DOCS}
    if "searchSentences" in query:
        return {"searchSentences": _SENTS.get(variables.get("d"), [])}
    if "entityFrequency" in query:
        return {"entityFrequency": _ENTS.get(variables.get("d"), [])}
    raise AssertionError(f"unexpected query: {query}")


@pytest.mark.asyncio
async def test_briefing_pack_assembles_cited_sources_passages_and_entities(monkeypatch):
    _install(monkeypatch, _briefing_handler)
    pack = await compose.briefing_pack("general relativity", max_documents=2)

    assert pack["topic"] == "general relativity"
    assert [s["document_id"] for s in pack["sources"]] == [
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    ]
    # 2 passages from docA + 1 from docB, each with a resolvable citation.
    assert pack["coverage"] == {"documents": 2, "passages": 3}
    first = pack["key_passages"][0]
    assert first["text"] == "In general relativity gravity is curvature."
    assert first["cite"] == {
        "document_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "sentence_ordinal": 12,
        "ref": "The Field Equations (1915) · sentence 12",
    }
    # Entities merged across the pack's documents and sorted by count.
    einstein = next(e for e in pack["entities"] if e["text"] == "Albert Einstein")
    assert einstein["count"] == 8  # 5 + 3
    assert pack["entities"][0]["text"] == "Albert Einstein"  # highest count first
    assert pack["gaps"] == []
    assert "source before synthesis" in pack["guidance"]


@pytest.mark.asyncio
async def test_briefing_pack_reports_gaps_when_nothing_matches(monkeypatch):
    def handler(query, variables):
        if "fullTextSearch" in query:
            return {"fullTextSearch": []}
        raise AssertionError("should not query passages when no documents match")

    _install(monkeypatch, handler)
    pack = await compose.briefing_pack("phlogiston dynamics")
    assert pack["sources"] == []
    assert pack["key_passages"] == []
    assert pack["gaps"] and "No documents" in pack["gaps"][0]


@pytest.mark.asyncio
async def test_briefing_pack_reports_per_document_gap(monkeypatch):
    def handler(query, variables):
        if "fullTextSearch" in query:
            return {
                "fullTextSearch": [
                    {
                        "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "title": "Empty",
                        "rank": 0.1,
                    }
                ]
            }
        if "searchSentences" in query:
            return {"searchSentences": []}  # doc matched the topic but has no literal passage
        if "entityFrequency" in query:
            return {"entityFrequency": []}
        raise AssertionError(query)

    _install(monkeypatch, handler)
    pack = await compose.briefing_pack("general relativity", max_documents=1)
    assert pack["coverage"]["passages"] == 0
    assert any("Empty" in g for g in pack["gaps"])


@pytest.mark.asyncio
async def test_briefing_pack_forwards_caller_bearer(monkeypatch):
    router = _install(monkeypatch, _briefing_handler)
    corpus.caller_token.set("tok-xyz")
    await compose.briefing_pack("general relativity", max_documents=2)
    assert router.headers and all(
        h.get("Authorization") == "Bearer tok-xyz" for h in router.headers
    )


# --- entity_dossier ---------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_dossier_groups_mentions_by_document(monkeypatch):
    mentions = [
        {
            "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "title": "Paper A",
            "ordinal": 1,
            "pre": "",
            "hit": "Einstein",
            "post": " wrote.",
        },
        {
            "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "title": "Paper A",
            "ordinal": 9,
            "pre": "and ",
            "hit": "Einstein",
            "post": " again.",
        },
        {
            "documentId": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "title": "Paper B",
            "ordinal": 4,
            "pre": "",
            "hit": "Einstein",
            "post": " noted.",
        },
    ]

    def handler(query, variables):
        assert "searchSentences" in query
        return {"searchSentences": mentions}

    _install(monkeypatch, handler)
    dossier = await compose.entity_dossier("Einstein")
    assert dossier["total_mentions"] == 3
    docs = {d["document_id"]: d["mention_count"] for d in dossier["documents"]}
    assert docs == {
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": 2,
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb": 1,
    }
    assert (
        dossier["documents"][0]["document_id"] == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )  # most mentions first
    assert dossier["mentions"][0]["cite"]["sentence_ordinal"] == 1


@pytest.mark.asyncio
async def test_entity_dossier_reports_gap_when_absent(monkeypatch):
    _install(monkeypatch, lambda q, v: {"searchSentences": []})
    dossier = await compose.entity_dossier("Nobody")
    assert dossier["total_mentions"] == 0
    assert dossier["gaps"] and "No mentions" in dossier["gaps"][0]


# --- document_evidence ------------------------------------------------------


@pytest.mark.asyncio
async def test_document_evidence_returns_metadata_and_cited_passages(monkeypatch):
    def handler(query, variables):
        if "document(id" in query:
            return {
                "document": {
                    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "title": "Paper A",
                    "docType": "pdf",
                }
            }
        if "searchSentences" in query:
            return {
                "searchSentences": [
                    {
                        "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "title": "Paper A",
                        "ordinal": 7,
                        "pre": "the ",
                        "hit": "geodesic",
                        "post": " equation.",
                    },
                ]
            }
        raise AssertionError(query)

    _install(monkeypatch, handler)
    evidence = await compose.document_evidence(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", focus="geodesic"
    )
    assert evidence["document"]["title"] == "Paper A"
    assert evidence["passages"][0]["text"] == "the geodesic equation."
    assert evidence["passages"][0]["cite"]["ref"] == "Paper A · sentence 7"
    assert evidence["gaps"] == []
