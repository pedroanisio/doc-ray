"""Shared briefing composition stays adapter-neutral and gap-honest."""

from __future__ import annotations

import pytest

from doc_ray.application.briefing import build_briefing_pack


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
