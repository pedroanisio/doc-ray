"""Multi-word fan-out retrieval and response-size budgets for the MCP compose tools (no network)."""

from __future__ import annotations

from unittest.mock import AsyncMock

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


#: #33 fallback notice added when the semantic probe finds no visible vectors.
_NO_VECTORS_GAP = (
    "No sentence vectors are visible in your corpus yet; ranked passages by "
    "full-text/keyword match instead."
)


def _install(monkeypatch, handler):
    # #33: briefing_pack probes semanticSearchSentences first; these lexical
    # fixtures model a corpus with no vectors, so the probe returns empty and the
    # shared service falls back to lexical retrieval (the ranking under test here).
    def routed(query, variables):
        if "semanticSearchSentences" in query:
            return {"semanticSearchSentences": []}
        return handler(query, variables)

    router = _Router(routed)
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=router))
    return router


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


_ONE_DOC = [
    {
        "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "title": "Relativity Papers",
        "rank": 0.9,
    }
]


@pytest.mark.asyncio
async def test_briefing_pack_multiword_topic_matches_by_term_fanout(monkeypatch):
    # The full phrase matches nothing, but individual content terms do; the pack
    # must still ground passages instead of reporting a gap. Stopwords ("the")
    # must not be searched.
    sents_by_term = {
        "einstein": [
            _sent(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "Relativity Papers",
                5,
                "Einstein",
                post=" field insight.",
            )
        ],
        "field": [
            _sent(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "Relativity Papers",
                5,
                "field",
                pre="Einstein ",
                post=" insight.",
            ),
            _sent(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "Relativity Papers",
                9,
                "field",
                post=" equations alone.",
            ),
        ],
    }
    router = _install(monkeypatch, _fanout_handler(_ONE_DOC, sents_by_term))
    pack = await compose.briefing_pack("the einstein field equations", max_documents=1)

    searched = [c["variables"]["t"] for c in router.calls if "searchSentences" in c["query"]]
    assert "the" not in searched  # stopwords never fan out
    assert pack["gaps"] == [_NO_VECTORS_GAP]  # lexical fallback reported (no vectors)
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
        "field equations": [
            _sent(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 33, "field equations"
            )
        ],
        "field": [
            _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 5, "field"),
            _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 9, "field"),
        ],
        "equations": [
            _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 5, "equations")
        ],
    }
    _install(monkeypatch, _fanout_handler(_ONE_DOC, sents_by_term))
    pack = await compose.briefing_pack("field equations", max_documents=1, passages_per_document=3)

    ordinals = [p["sentence_ordinal"] for p in pack["key_passages"]]
    # Phrase hit (33) first, then the two-term sentence (5), then the one-term (9).
    assert ordinals == [33, 5, 9]
    assert pack["key_passages"][0]["matched_terms"] == ["field equations"]


@pytest.mark.asyncio
async def test_briefing_pack_single_term_topic_issues_single_search(monkeypatch):
    sents_by_term = {
        "relativity": [
            _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 3, "relativity")
        ]
    }
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
    sents_by_term = {
        "relativity": [
            _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 3, "relativity")
        ]
    }
    _install(monkeypatch, _fanout_handler(_ONE_DOC, sents_by_term, ents))
    pack = await compose.briefing_pack("relativity", max_documents=1)

    texts = [e["text"] for e in pack["entities"]]
    assert texts == ["MCP", "VS Code", "Einstein"]  # noise dropped, count order kept


@pytest.mark.asyncio
async def test_document_evidence_multiword_focus_matches_by_term_fanout(monkeypatch):
    sents_by_term = {
        "best": [
            _sent(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "Paper A",
                95,
                "best",
                post=" practices for keys.",
            )
        ],
        "practices": [
            _sent(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "Paper A",
                95,
                "practices",
                pre="best ",
                post=" for keys.",
            ),
            _sent(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "Paper A",
                12,
                "practices",
                pre="operational ",
            ),
        ],
    }

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
            return {"searchSentences": sents_by_term.get(variables["t"], [])}
        raise AssertionError(query)

    _install(monkeypatch, handler)
    evidence = await compose.document_evidence(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", focus="best practices"
    )

    assert evidence["gaps"] == []
    assert [p["sentence_ordinal"] for p in evidence["passages"]] == [95, 12]
    assert evidence["passages"][0]["matched_terms"] == ["best", "practices"]


@pytest.mark.asyncio
async def test_document_evidence_gap_reports_terms_tried(monkeypatch):
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
            return {"searchSentences": []}
        raise AssertionError(query)

    _install(monkeypatch, handler)
    evidence = await compose.document_evidence(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", focus="structured output"
    )

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
        _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 1, _forty("one")),
        _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 2, _forty("two")),
        _sent("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "Relativity Papers", 3, _forty("three")),
    ]
}


@pytest.mark.asyncio
async def test_briefing_pack_clips_oversized_passage_text(monkeypatch):
    monkeypatch.setattr(compose, "_MAX_PASSAGE_CHARS", 50)
    long_sent = _sent(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "Relativity Papers",
        7,
        "relativity",
        post=" " + "z" * 200,
    )
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
            return {
                "document": {
                    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "title": "Relativity Papers",
                    "docType": "pdf",
                }
            }
        if "searchSentences" in query:
            return {"searchSentences": _BUDGET_SENTS["relativity"]}
        raise AssertionError(query)

    _install(monkeypatch, handler)
    evidence = await compose.document_evidence(
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", focus="relativity"
    )

    assert [p["sentence_ordinal"] for p in evidence["passages"]] == [1]
    assert any("dropped 2" in gap for gap in evidence["gaps"])


@pytest.mark.asyncio
async def test_entity_dossier_budget_keeps_tallies_and_reports(monkeypatch):
    monkeypatch.setattr(compose, "_DOSSIER_MENTION_BUDGET", 60)
    roster = [
        {
            "documentId": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "title": "Relativity Papers",
            "rank": 0.9,
        }
    ]

    def handler(query, variables):
        if "fullTextSearch" in query:
            return {"fullTextSearch": roster}
        return {"searchSentences": _BUDGET_SENTS["relativity"]}

    _install(monkeypatch, handler)
    dossier = await compose.entity_dossier("relativity")

    # cited_mentions and per-document tallies reflect everything CITED; only the
    # returned mention texts are budgeted.
    assert dossier["cited_mentions"] == 3
    assert dossier["documents"][0]["cited_mention_count"] == 3
    assert len(dossier["mentions"]) == 1
    assert any("dropped 2" in gap for gap in dossier["gaps"])
