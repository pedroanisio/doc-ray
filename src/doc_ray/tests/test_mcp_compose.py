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
    {"documentId": "docA", "title": "The Field Equations (1915)", "rank": 0.9},
    {"documentId": "docB", "title": "Schwarzschild Solution", "rank": 0.7},
]
_SENTS = {
    "docA": [
        {
            "documentId": "docA",
            "title": "The Field Equations (1915)",
            "ordinal": 12,
            "pre": "In ",
            "hit": "general relativity",
            "post": " gravity is curvature.",
        },
        {
            "documentId": "docA",
            "title": "The Field Equations (1915)",
            "ordinal": 40,
            "pre": "",
            "hit": "General relativity",
            "post": " predicts light bending.",
        },
    ],
    "docB": [
        {
            "documentId": "docB",
            "title": "Schwarzschild Solution",
            "ordinal": 3,
            "pre": "The ",
            "hit": "general relativity",
            "post": " metric is exact.",
        },
    ],
}
_ENTS = {
    "docA": [
        {"text": "Albert Einstein", "entityType": "PERSON", "count": 5},
        {"text": "Mercury", "entityType": "GPE", "count": 2},
    ],
    "docB": [
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
    assert [s["document_id"] for s in pack["sources"]] == ["docA", "docB"]
    # 2 passages from docA + 1 from docB, each with a resolvable citation.
    assert pack["coverage"] == {"documents": 2, "passages": 3}
    first = pack["key_passages"][0]
    assert first["text"] == "In general relativity gravity is curvature."
    assert first["cite"] == {
        "document_id": "docA",
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
            return {"fullTextSearch": [{"documentId": "docA", "title": "Empty", "rank": 0.1}]}
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


# --- multi-word retrieval: per-term fan-out ---------------------------------


def _sent(doc_id, title, ordinal, hit, pre="", post=""):
    return {
        "documentId": doc_id,
        "title": title,
        "ordinal": ordinal,
        "pre": pre,
        "hit": hit,
        "post": post,
    }


def _fanout_handler(docs, sents_by_term, ents=None):
    """Route searchSentences by the exact term searched (variables['t'])."""

    def handler(query, variables):
        if "fullTextSearch" in query:
            return {"fullTextSearch": docs}
        if "searchSentences" in query:
            return {"searchSentences": sents_by_term.get(variables["t"], [])}
        if "entityFrequency" in query:
            return {"entityFrequency": ents or []}
        raise AssertionError(f"unexpected query: {query}")

    return handler


_ONE_DOC = [{"documentId": "docA", "title": "Relativity Papers", "rank": 0.9}]


@pytest.mark.asyncio
async def test_briefing_pack_multiword_topic_matches_by_term_fanout(monkeypatch):
    # The full phrase matches nothing, but individual content terms do; the pack
    # must still ground passages instead of reporting a gap. Stopwords ("the")
    # must not be searched.
    sents_by_term = {
        "einstein": [_sent("docA", "Relativity Papers", 5, "Einstein", post=" field insight.")],
        "field": [
            _sent("docA", "Relativity Papers", 5, "field", pre="Einstein ", post=" insight."),
            _sent("docA", "Relativity Papers", 9, "field", post=" equations alone."),
        ],
    }
    router = _install(monkeypatch, _fanout_handler(_ONE_DOC, sents_by_term))
    pack = await compose.briefing_pack("the einstein field equations", max_documents=1)

    searched = [c["variables"]["t"] for c in router.calls if "searchSentences" in c["query"]]
    assert "the" not in searched  # stopwords never fan out
    assert pack["gaps"] == []
    assert pack["coverage"]["passages"] == 2
    # Sentence 5 matched two distinct terms and must outrank sentence 9 (one term).
    first, second = pack["key_passages"]
    assert first["sentence_ordinal"] == 5
    assert first["matched_terms"] == ["einstein", "field"]
    assert second["sentence_ordinal"] == 9
    assert second["matched_terms"] == ["field"]
    assert "fan-out" in pack["retrieval"]


@pytest.mark.asyncio
async def test_briefing_pack_phrase_match_outranks_term_matches(monkeypatch):
    sents_by_term = {
        "field equations": [_sent("docA", "Relativity Papers", 33, "field equations")],
        "field": [
            _sent("docA", "Relativity Papers", 5, "field"),
            _sent("docA", "Relativity Papers", 9, "field"),
        ],
        "equations": [_sent("docA", "Relativity Papers", 5, "equations")],
    }
    _install(monkeypatch, _fanout_handler(_ONE_DOC, sents_by_term))
    pack = await compose.briefing_pack("field equations", max_documents=1, passages_per_document=3)

    ordinals = [p["sentence_ordinal"] for p in pack["key_passages"]]
    # Phrase hit (33) first, then the two-term sentence (5), then the one-term (9).
    assert ordinals == [33, 5, 9]
    assert pack["key_passages"][0]["matched_terms"] == ["field equations"]


@pytest.mark.asyncio
async def test_briefing_pack_single_term_topic_issues_single_search(monkeypatch):
    sents_by_term = {"relativity": [_sent("docA", "Relativity Papers", 3, "relativity")]}
    router = _install(monkeypatch, _fanout_handler(_ONE_DOC, sents_by_term))
    pack = await compose.briefing_pack("relativity", max_documents=1)

    searched = [c["variables"]["t"] for c in router.calls if "searchSentences" in c["query"]]
    assert searched == ["relativity"]  # no redundant fan-out for a single term
    assert pack["coverage"]["passages"] == 1


@pytest.mark.asyncio
async def test_briefing_pack_filters_numeric_and_generic_entities(monkeypatch):
    ents = [
        {"text": "message", "entityType": "UNKNOWN", "count": 283},  # generic lowercase noun
        {"text": "2", "entityType": "CARDINAL", "count": 161},  # bare number
        {"text": "two", "entityType": "CARDINAL", "count": 41},  # number word
        {"text": "data", "entityType": None, "count": 100},  # untyped lowercase
        {"text": "the", "entityType": "PRODUCT", "count": 9},  # stopword, any type
        {"text": "MCP", "entityType": "PRODUCT", "count": 62},
        {"text": "VS Code", "entityType": "PRODUCT", "count": 40},
        {"text": "Einstein", "entityType": "PERSON", "count": 5},
    ]
    sents_by_term = {"relativity": [_sent("docA", "Relativity Papers", 3, "relativity")]}
    _install(monkeypatch, _fanout_handler(_ONE_DOC, sents_by_term, ents))
    pack = await compose.briefing_pack("relativity", max_documents=1)

    texts = [e["text"] for e in pack["entities"]]
    assert texts == ["MCP", "VS Code", "Einstein"]  # noise dropped, count order kept


@pytest.mark.asyncio
async def test_document_evidence_multiword_focus_matches_by_term_fanout(monkeypatch):
    sents_by_term = {
        "best": [_sent("docA", "Paper A", 95, "best", post=" practices for keys.")],
        "practices": [
            _sent("docA", "Paper A", 95, "practices", pre="best ", post=" for keys."),
            _sent("docA", "Paper A", 12, "practices", pre="operational "),
        ],
    }

    def handler(query, variables):
        if "document(id" in query:
            return {"document": {"id": "docA", "title": "Paper A", "docType": "pdf"}}
        if "searchSentences" in query:
            return {"searchSentences": sents_by_term.get(variables["t"], [])}
        raise AssertionError(query)

    _install(monkeypatch, handler)
    evidence = await compose.document_evidence("docA", focus="best practices")

    assert evidence["gaps"] == []
    assert [p["sentence_ordinal"] for p in evidence["passages"]] == [95, 12]
    assert evidence["passages"][0]["matched_terms"] == ["best", "practices"]


@pytest.mark.asyncio
async def test_document_evidence_gap_reports_terms_tried(monkeypatch):
    def handler(query, variables):
        if "document(id" in query:
            return {"document": {"id": "docA", "title": "Paper A", "docType": "pdf"}}
        if "searchSentences" in query:
            return {"searchSentences": []}
        raise AssertionError(query)

    _install(monkeypatch, handler)
    evidence = await compose.document_evidence("docA", focus="structured output")

    assert evidence["passages"] == []
    assert evidence["gaps"], "an empty result must be reported as a gap"
    assert "terms tried: structured, output" in evidence["gaps"][0]


# --- response-size budgets --------------------------------------------------


def _forty(tag):
    """A deterministic 40-char passage body."""
    body = f"relativity {tag} "
    return body + "a" * (40 - len(body))


_BUDGET_SENTS = {
    "relativity": [
        _sent("docA", "Relativity Papers", 1, _forty("one")),
        _sent("docA", "Relativity Papers", 2, _forty("two")),
        _sent("docA", "Relativity Papers", 3, _forty("three")),
    ]
}


@pytest.mark.asyncio
async def test_briefing_pack_clips_oversized_passage_text(monkeypatch):
    monkeypatch.setattr(compose, "_MAX_PASSAGE_CHARS", 50)
    long_sent = _sent("docA", "Relativity Papers", 7, "relativity", post=" " + "z" * 200)
    _install(monkeypatch, _fanout_handler(_ONE_DOC, {"relativity": [long_sent]}))
    pack = await compose.briefing_pack("relativity", max_documents=1)

    (passage,) = pack["key_passages"]
    assert passage["text"].startswith("relativity ")
    assert "[+161 chars]" in passage["text"]  # 211-char body clipped at 50, marked
    assert passage["cite"]["sentence_ordinal"] == 7  # citation survives clipping


@pytest.mark.asyncio
async def test_briefing_pack_budget_drops_excess_passages_and_reports(monkeypatch):
    monkeypatch.setattr(compose, "_BRIEFING_PASSAGE_BUDGET", 60)
    _install(monkeypatch, _fanout_handler(_ONE_DOC, _BUDGET_SENTS))
    pack = await compose.briefing_pack("relativity", max_documents=1, passages_per_document=3)

    # 3 passages of 40 chars against a 60-char budget: the first always survives,
    # the rest are dropped and the drop is reported — never silent.
    assert pack["coverage"]["passages"] == 1
    assert [p["sentence_ordinal"] for p in pack["key_passages"]] == [1]
    assert any("dropped 2" in gap for gap in pack["gaps"])


@pytest.mark.asyncio
async def test_document_evidence_budget_drops_excess_and_reports(monkeypatch):
    monkeypatch.setattr(compose, "_EVIDENCE_PASSAGE_BUDGET", 60)

    def handler(query, variables):
        if "document(id" in query:
            return {"document": {"id": "docA", "title": "Relativity Papers", "docType": "pdf"}}
        if "searchSentences" in query:
            return {"searchSentences": _BUDGET_SENTS["relativity"]}
        raise AssertionError(query)

    _install(monkeypatch, handler)
    evidence = await compose.document_evidence("docA", focus="relativity")

    assert [p["sentence_ordinal"] for p in evidence["passages"]] == [1]
    assert any("dropped 2" in gap for gap in evidence["gaps"])


@pytest.mark.asyncio
async def test_entity_dossier_budget_keeps_tallies_and_reports(monkeypatch):
    monkeypatch.setattr(compose, "_DOSSIER_MENTION_BUDGET", 60)
    _install(monkeypatch, lambda q, v: {"searchSentences": _BUDGET_SENTS["relativity"]})
    dossier = await compose.entity_dossier("relativity")

    # total_mentions and per-document tallies reflect everything FOUND; only the
    # returned mention texts are budgeted.
    assert dossier["total_mentions"] == 3
    assert dossier["documents"][0]["mention_count"] == 3
    assert len(dossier["mentions"]) == 1
    assert any("dropped 2" in gap for gap in dossier["gaps"])


# --- entity_dossier ---------------------------------------------------------


@pytest.mark.asyncio
async def test_entity_dossier_groups_mentions_by_document(monkeypatch):
    mentions = [
        {
            "documentId": "docA",
            "title": "Paper A",
            "ordinal": 1,
            "pre": "",
            "hit": "Einstein",
            "post": " wrote.",
        },
        {
            "documentId": "docA",
            "title": "Paper A",
            "ordinal": 9,
            "pre": "and ",
            "hit": "Einstein",
            "post": " again.",
        },
        {
            "documentId": "docB",
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
    assert docs == {"docA": 2, "docB": 1}
    assert dossier["documents"][0]["document_id"] == "docA"  # most mentions first
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
            return {"document": {"id": "docA", "title": "Paper A", "docType": "pdf"}}
        if "searchSentences" in query:
            return {
                "searchSentences": [
                    {
                        "documentId": "docA",
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
    evidence = await compose.document_evidence("docA", focus="geodesic")
    assert evidence["document"]["title"] == "Paper A"
    assert evidence["passages"][0]["text"] == "the geodesic equation."
    assert evidence["passages"][0]["cite"]["ref"] == "Paper A · sentence 7"
    assert evidence["gaps"] == []
