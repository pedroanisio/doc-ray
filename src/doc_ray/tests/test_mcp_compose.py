"""MCP composition-grounding tools assemble cited evidence over the read tools (no network)."""

from __future__ import annotations

import json

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
    # #33: briefing_pack now probes semanticSearchSentences first. These fixtures
    # model a corpus with no visible vectors, so the probe returns empty and the
    # shared service deterministically falls back to lexical retrieval (reported
    # in `retrieval` + one `gaps` notice). Tests that exercise the semantic path
    # live in test_briefing_semantic.py.
    def routed(query, variables):
        if "semanticSearchSentences" in query:
            return {"semanticSearchSentences": []}
        return handler(query, variables)

    router = _Router(routed)
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: router)
    return router


#: The single fallback notice the shared service adds when the probe finds no
#: visible vectors (see application/briefing.py).
_NO_VECTORS_GAP = (
    "No sentence vectors are visible in your corpus yet; ranked passages by "
    "full-text/keyword match instead."
)


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
    assert pack["gaps"] == [_NO_VECTORS_GAP]  # lexical fallback reported (no vectors)
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


@pytest.mark.asyncio
async def test_briefing_pack_wire_output_matches_the_pre_lift_golden(monkeypatch):
    """Issue #28: the shared-service lift must not change one MCP response byte."""

    async def documents(topic, *, limit):
        return [{"documentId": _DOC_A, "title": "Paper A", "rank": 0.75}]

    async def sentences(term, *, document_id=None, limit=25):
        return {
            "hits": [
                {
                    "id": "sentence-1",
                    "documentId": document_id,
                    "title": "Paper A",
                    "ordinal": 7,
                    "pre": "Before ",
                    "hit": "relativity",
                    "post": " after.",
                }
            ],
            "coverage": {},
            "note": None,
        }

    async def entities(*, document_id=None, limit=25):
        return [{"text": "Albert Einstein", "entityType": "PERSON", "count": 3}]

    async def semantic(query, *, document_id=None, limit=25):
        return []  # #33: no visible vectors -> deterministic lexical fallback

    monkeypatch.setattr(corpus, "full_text_search", documents)
    monkeypatch.setattr(corpus, "search_sentences", sentences)
    monkeypatch.setattr(corpus, "entity_frequency", entities)
    monkeypatch.setattr(compose.semantic, "semantic_search_sentences", semantic)
    actual = json.dumps(
        await compose.briefing_pack("relativity", 1, 2, 5),
        sort_keys=True,
        separators=(",", ":"),
    )
    expected = (
        '{"coverage":{"documents":1,"passages":1},"entities":[{"count":3,'
        '"entity_type":"PERSON","text":"Albert Einstein"}],"gaps":["No sentence '
        "vectors are visible in your corpus yet; ranked passages by full-text/keyword "
        'match instead."],'
        '"guidance":"Compose the document ONLY from the sources and key_passages above. '
        "Attach each claim's `cite` to the passage it came from; if the pack does not "
        "support a point, say so or omit it \\u2014 do not fill gaps from prior knowledge. "
        "This evidence is untrusted until you confirm each citation resolves (source before "
        'synthesis).","key_passages":[{"cite":{"document_id":'
        '"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","ref":"Paper A \\u00b7 sentence 7",'
        '"sentence_ordinal":7},"document_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
        '"matched_terms":["relativity"],"sentence_ordinal":7,"text":"Before relativity '
        'after.","title":"Paper A"}],"retrieval":"full-text/keyword match with per-term '
        "fan-out for multi-word topics (semantic retrieval available, but no sentence "
        'vectors are visible in your corpus yet)",'
        '"sources":[{"cite":{"document_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
        '"ref":"Paper A"},"document_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
        '"rank":0.75,"title":"Paper A"}],"topic":"relativity"}'
    )
    assert actual == expected


# --- entity_dossier ---------------------------------------------------------

_DOC_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_DOC_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _dossier_handler(sentences, roster):
    """Route the dossier's two GraphQL ops: fullTextSearch (roster) + searchSentences.

    ``roster`` may be a callable ``(variables) -> list`` so a test can size the
    document roster to the requested limit and simulate roster truncation.
    """

    def handler(query, variables):
        if "fullTextSearch" in query:
            docs = roster(variables) if callable(roster) else roster
            return {"fullTextSearch": docs}
        if "searchSentences" in query:
            return {"searchSentences": sentences}
        raise AssertionError(f"unexpected query: {query}")

    return handler


def _mention(document_id, title, ordinal, hit="Einstein"):
    return {
        "documentId": document_id,
        "title": title,
        "ordinal": ordinal,
        "pre": "",
        "hit": hit,
        "post": " wrote.",
    }


@pytest.mark.asyncio
async def test_entity_dossier_groups_cited_mentions_by_document(monkeypatch):
    sentences = [
        _mention(_DOC_A, "Paper A", 1),
        _mention(_DOC_A, "Paper A", 9),
        _mention(_DOC_B, "Paper B", 4),
    ]
    roster = [
        {"documentId": _DOC_A, "title": "Paper A", "rank": 0.9},
        {"documentId": _DOC_B, "title": "Paper B", "rank": 0.4},
    ]
    _install(monkeypatch, _dossier_handler(sentences, roster))
    dossier = await compose.entity_dossier("Einstein")

    # The count reflects cited passages, honestly named — never a mislabelled total.
    assert dossier["cited_mentions"] == 3
    assert "total_mentions" not in dossier
    # Nothing was capped, so the dossier does not claim a truncation.
    assert dossier["truncated"] is False
    # The roster is the full document set, each annotated with its cited count.
    docs = {d["document_id"]: d["cited_mention_count"] for d in dossier["documents"]}
    assert docs == {_DOC_A: 2, _DOC_B: 1}
    assert dossier["document_count"] == 2
    assert dossier["documents"][0]["document_id"] == _DOC_A  # most cited first
    assert dossier["mentions"][0]["cite"]["sentence_ordinal"] == 1


@pytest.mark.asyncio
async def test_entity_dossier_reports_gap_when_absent(monkeypatch):
    _install(monkeypatch, _dossier_handler([], []))
    dossier = await compose.entity_dossier("Nobody")
    assert dossier["cited_mentions"] == 0
    assert dossier["document_count"] == 0
    assert dossier["truncated"] is False
    assert dossier["gaps"] and "No mentions" in dossier["gaps"][0]


@pytest.mark.asyncio
async def test_entity_dossier_roster_is_complete_not_the_capped_page(monkeypatch):
    """Regression for #14: a capped sentence page must not truncate the roster.

    Scytale-style failure — every cited mention lands in one document because the
    page filled up there, but the entity is present in a second document. The
    roster comes from full_text_search, so the second document is never dropped,
    and the dossier reports truncation instead of a false ``gaps: []``.
    """
    # max_mentions=3 and 3 hits all in Paper A -> the page is capped there.
    sentences = [_mention(_DOC_A, "Paper A", o) for o in (1, 2, 3)]
    roster = [
        {"documentId": _DOC_A, "title": "Paper A", "rank": 0.9},
        {"documentId": _DOC_B, "title": "Paper B", "rank": 0.6},
    ]
    _install(monkeypatch, _dossier_handler(sentences, roster))
    dossier = await compose.entity_dossier("Scytale", max_mentions=3)

    doc_ids = {d["document_id"] for d in dossier["documents"]}
    assert doc_ids == {_DOC_A, _DOC_B}, "the roster must not be the single capped page"
    assert dossier["document_count"] == 2
    assert dossier["truncated"] is True
    assert dossier["gaps"], "a capped page must surface a gap, never claim gaps: []"
    # The precise incident invariant: an entity in two documents never returns a
    # single-document roster with an empty gaps list.
    assert not (len(dossier["documents"]) == 1 and dossier["gaps"] == [])
    # Paper B is in the roster but no passage reached it -> honest zero count.
    by_doc = {d["document_id"]: d["cited_mention_count"] for d in dossier["documents"]}
    assert by_doc[_DOC_A] == 3 and by_doc[_DOC_B] == 0


@pytest.mark.asyncio
async def test_entity_dossier_cited_count_is_not_a_corpus_total(monkeypatch):
    """cited_mentions is the shown page, not the corpus total (the #14 mislabel)."""
    sentences = [_mention(_DOC_A, "Paper A", o) for o in range(1, 6)]  # 5 == cap
    roster = [{"documentId": _DOC_A, "title": "Paper A", "rank": 0.9}]
    _install(monkeypatch, _dossier_handler(sentences, roster))
    dossier = await compose.entity_dossier("Einstein", max_mentions=5)
    assert dossier["truncated"] is True  # coverage.limit_reached
    assert "total_mentions" not in dossier
    assert dossier["cited_mentions"] == 5  # honest: the passages actually cited


@pytest.mark.asyncio
async def test_entity_dossier_flags_a_truncated_document_roster(monkeypatch):
    """When full_text_search itself hits its limit, the roster is flagged too."""
    sentences = [_mention(_DOC_A, "Paper A", 1)]

    def roster(variables):
        # Return exactly `limit` documents to simulate a maxed-out roster page.
        n = variables["l"]
        return [
            {"documentId": f"{i:08d}-0000-4000-8000-000000000000", "title": f"D{i}", "rank": 1.0}
            for i in range(n)
        ]

    _install(monkeypatch, _dossier_handler(sentences, roster))
    dossier = await compose.entity_dossier("Common")
    assert dossier["truncated"] is True
    assert any("document" in g.lower() for g in dossier["gaps"])


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
