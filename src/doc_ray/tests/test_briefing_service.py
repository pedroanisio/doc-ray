"""Shared briefing composition stays adapter-neutral and gap-honest."""

from __future__ import annotations

import pytest

from doc_ray.application.briefing import SemanticUnavailable, build_briefing_pack


class SeededReader:
    """Small in-memory read port whose rows match the public service contract."""

    async def full_text_search(self, topic: str, *, limit: int) -> list[dict]:
        if topic == "missing":
            return []
        return [{"documentId": "doc-a", "title": "Paper A", "rank": 0.8}][:limit]

    async def search_sentences(self, term: str, *, document_id: str, limit: int) -> list[dict]:
        return [
            {
                "id": "sentence-a",
                "documentId": document_id,
                "title": "Paper A",
                "ordinal": 9,
                "pre": "Before ",
                "hit": term,
                "post": " after.",
            }
        ][:limit]

    async def entity_frequency(self, *, document_id: str, limit: int) -> list[dict]:
        assert document_id == "doc-a"
        return [{"text": "Ada Lovelace", "entityType": "PERSON", "count": 3}][:limit]


@pytest.mark.asyncio
async def test_shared_service_builds_the_full_cited_contract():
    pack = await build_briefing_pack("analytical engine", reader=SeededReader())

    assert pack["coverage"] == {"documents": 1, "passages": 1}
    assert pack["sources"][0]["cite"] == {"document_id": "doc-a", "ref": "Paper A"}
    assert pack["key_passages"][0]["cite"] == {
        "document_id": "doc-a",
        "sentence_ordinal": 9,
        "ref": "Paper A · sentence 9",
    }
    assert pack["entities"] == [{"text": "Ada Lovelace", "entity_type": "PERSON", "count": 3}]
    assert pack["gaps"] == []
    assert "semantic similarity retrieval is not yet available" in pack["retrieval"]


@pytest.mark.asyncio
async def test_shared_service_reports_an_ungroundable_topic():
    pack = await build_briefing_pack("missing", reader=SeededReader())

    assert pack["sources"] == []
    assert pack["key_passages"] == []
    assert pack["coverage"] == {"documents": 0, "passages": 0}
    assert pack["gaps"] and "No documents" in pack["gaps"][0]


@pytest.mark.asyncio
async def test_shared_service_never_silently_drops_passages_at_budget():
    class LargeReader(SeededReader):
        async def search_sentences(self, term: str, *, document_id: str, limit: int) -> list[dict]:
            return [
                {
                    "id": f"sentence-{ordinal}",
                    "documentId": document_id,
                    "title": "Paper A",
                    "ordinal": ordinal,
                    "pre": "",
                    "hit": term,
                    "post": "x" * 2_000,
                }
                for ordinal in range(limit)
            ]

    pack = await build_briefing_pack("budget", reader=LargeReader(), passages_per_document=20)

    assert len(pack["key_passages"]) < 20
    assert any("Response budget reached" in gap for gap in pack["gaps"])


# --------------------------------------------------------------------------
# Semantic / hybrid retrieval (#33): the shared service owns the ranking.
# --------------------------------------------------------------------------


class SemanticReader(SeededReader):
    """SeededReader + a learned-semantic sentence search under service control.

    ``hits`` are returned in similarity order (as the DB/adapter would); the
    service must preserve that order and not re-rank lexically. ``unavailable``
    simulates a host without the sentence stack; ``no_vectors`` simulates a stack
    present but nothing embedded/visible.
    """

    def __init__(self, *, hits=None, unavailable=False, no_vectors=False):
        self._hits = hits or []
        self._unavailable = unavailable
        self._no_vectors = no_vectors
        self.semantic_calls: list[tuple[str, str | None, int]] = []

    async def semantic_search_sentences(self, query, *, document_id=None, limit):
        self.semantic_calls.append((query, document_id, limit))
        if self._unavailable:
            raise SemanticUnavailable("sentence-transformers is not installed")
        if self._no_vectors:
            return []
        return [h for h in self._hits if document_id is None or h["documentId"] == document_id][
            :limit
        ]


_PARAPHRASE = "The mill weaves algebraic patterns just as the loom weaves flowers."


@pytest.mark.asyncio
async def test_semantic_passages_rank_by_meaning_not_shared_words():
    # The top passage is a paraphrase with NO lexical overlap with the topic —
    # lexical fan-out would miss it; semantic ranking surfaces it first.
    reader = SemanticReader(
        hits=[{"id": "s3", "documentId": "doc-a", "ordinal": 3, "text": _PARAPHRASE}]
    )
    pack = await build_briefing_pack("analytical engine", reader=reader)

    assert pack["key_passages"], "semantic hit must produce a passage"
    top = pack["key_passages"][0]
    assert top["text"] == _PARAPHRASE
    assert top["sentence_ordinal"] == 3
    assert top["cite"]["sentence_ordinal"] == 3  # cite handle preserved
    assert top["matched_terms"] == []  # semantic hits carry no lexical terms
    assert "semantic" in pack["retrieval"].lower()
    assert pack["gaps"] == []


@pytest.mark.asyncio
async def test_falls_back_to_lexical_when_the_semantic_stack_is_unavailable():
    reader = SemanticReader(unavailable=True)
    pack = await build_briefing_pack("analytical engine", reader=reader)

    # lexical KWIC passage (from SeededReader.search_sentences) still produced
    assert pack["key_passages"][0]["matched_terms"]  # lexical path populates terms
    assert "full-text" in pack["retrieval"].lower()
    assert any(
        "semantic" in gap.lower() and "unavailable" in gap.lower() for gap in pack["gaps"]
    ), pack["gaps"]


@pytest.mark.asyncio
async def test_falls_back_to_lexical_when_no_sentence_vectors_are_visible():
    reader = SemanticReader(no_vectors=True)
    pack = await build_briefing_pack("analytical engine", reader=reader)

    assert pack["key_passages"][0]["matched_terms"]  # lexical fallback ran
    assert "full-text" in pack["retrieval"].lower()
    assert any("vector" in gap.lower() for gap in pack["gaps"]), pack["gaps"]


@pytest.mark.asyncio
async def test_semantic_passages_are_budget_bounded_and_report_drops():
    big = [
        {"id": f"s{n}", "documentId": "doc-a", "ordinal": n, "text": "y" * 2_000} for n in range(20)
    ]
    reader = SemanticReader(hits=big)
    pack = await build_briefing_pack("budget", reader=reader, passages_per_document=20)

    assert len(pack["key_passages"]) < 20
    assert any("Response budget reached" in gap for gap in pack["gaps"])


@pytest.mark.asyncio
async def test_a_legacy_reader_without_semantic_search_stays_lexical():
    # A reader that predates #33 (no semantic_search_sentences) must keep working,
    # ranking lexically and saying so — never crash on the missing capability.
    pack = await build_briefing_pack("analytical engine", reader=SeededReader())
    assert pack["key_passages"][0]["matched_terms"]
    assert "semantic similarity retrieval is not yet available" in pack["retrieval"]
