"""GraphQL briefing contract delegates every read through the request's RLS connection."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from doc_ray.entrypoints.api import briefing_query
from doc_ray.entrypoints.api.schema import build_schema


class _Context(SimpleNamespace):
    def page_size(self, requested: int) -> int:
        return max(1, min(requested, 200))


_QUERY = """
query Briefing($topic: String!) {
  briefingPack(topic: $topic, maxDocuments: 2, passagesPerDocument: 3, maxEntities: 4) {
    topic retrieval guidance gaps
    coverage { documents passages }
    sources { documentId title rank cite { documentId sentenceOrdinal ref } }
    keyPassages {
      documentId title sentenceOrdinal text matchedTerms
      cite { documentId sentenceOrdinal ref }
    }
    entities { text entityType count }
  }
}
"""


def test_briefing_pack_schema_exposes_the_full_contract():
    sdl = build_schema(debug=True).as_str()

    assert "briefingPack(" in sdl
    for name in (
        "type BriefingPack",
        "type BriefingSource",
        "type BriefingPassage",
        "type BriefingEntity",
        "type BriefingCoverage",
        "type Cite",
    ):
        assert name in sdl


def test_briefing_pack_uses_only_the_callers_rls_connection(monkeypatch):
    calls: list[tuple[str, object]] = []

    async def fulltext(conn, topic, *, limit):
        calls.append(("documents", conn))
        return [{"doc_id": f"doc-{conn}", "title": f"Paper {conn}", "rank": 0.9}]

    async def sentences(conn, term, *, document_id=None, limit=100, after=None):
        calls.append(("sentences", conn))
        return [
            {
                "id": f"sentence-{conn}",
                "doc_id": document_id,
                "title": f"Paper {conn}",
                "ordinal": 4,
                "pre": "",
                "hit": term,
                "post": " is grounded.",
            }
        ]

    async def entities(conn, *, document_id=None, entity_type=None, limit=100, after=None):
        calls.append(("entities", conn))
        return [{"text": f"Entity {conn}", "entity_type": "ORG", "count": 2}]

    monkeypatch.setattr(briefing_query.read, "fulltext_search", fulltext)
    monkeypatch.setattr(briefing_query.read, "search_sentences", sentences)
    monkeypatch.setattr(briefing_query.read, "entity_frequency", entities)
    schema = build_schema()

    async def execute_for(principal_conn: str):
        return await schema.execute(
            _QUERY,
            variable_values={"topic": "evidence"},
            context_value=_Context(conn=principal_conn),
        )

    first = asyncio.run(execute_for("alice"))
    second = asyncio.run(execute_for("bob"))

    assert first.errors is None, first.errors
    assert second.errors is None, second.errors
    assert first.data["briefingPack"]["sources"][0]["documentId"] == "doc-alice"
    assert second.data["briefingPack"]["sources"][0]["documentId"] == "doc-bob"
    assert all(conn == "alice" for _kind, conn in calls[:3])
    assert all(conn == "bob" for _kind, conn in calls[3:])
