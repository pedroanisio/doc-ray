"""Read-surface contracts for the mention confidence mention-confidence review queue."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from doc_ray.entrypoints.api.schema import build_schema

_DOC_ID = "11111111-1111-1111-1111-111111111111"


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return _Cursor(self.rows)


class _Context(SimpleNamespace):
    def page_size(self, requested: int) -> int:
        return max(1, min(requested, 200))


def test_uncertain_mentions_schema_is_bounded_filterable_and_pageable():
    sdl = build_schema(debug=True).as_str()
    assert (
        "uncertainMentions(documentId: ID!, limit: Int! = 100, "
        "entityType: String = null, after: String = null): [UncertainMention!]!"
    ) in sdl
    for field in (
        "id: ID!",
        "documentId: ID!",
        "charStart: Int!",
        "charEnd: Int!",
        "surface: String!",
        "entityType: String!",
        "confidence: Float!",
        "reasons: [String!]!",
        "method: String!",
        "cursor: String!",
    ):
        assert field in sdl


def test_uncertain_mentions_reads_materialized_rank_and_excludes_corrected_spans():
    from doc_ray.adapters.persistence_mention_confidence_read import uncertain_mentions

    rows = [
        {
            "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
            "document_id": _DOC_ID,
            "char_start": 10,
            "char_end": 17,
            "surface": "Arrakis",
            "entity_type": "LOC",
            "confidence": 0.25,
            "confidence_reasons": ["entity type disagrees across the tenant corpus"],
            "confidence_method": "heuristic:v1",
        }
    ]
    conn = _Connection(rows)

    first = asyncio.run(uncertain_mentions(conn, _DOC_ID, entity_type="LOC", limit=25, after=None))
    second = asyncio.run(
        uncertain_mentions(conn, _DOC_ID, entity_type="LOC", limit=25, after=first[0]["cursor"])
    )

    sql, params = conn.calls[0]
    assert "em.confidence IS NOT NULL" in sql
    assert "entity_mention_correction" in sql and "NOT EXISTS" in sql
    assert "ORDER BY em.confidence, em.id" in sql
    assert "count(" not in sql.lower() and "group by" not in sql.lower()
    assert params["doc"] == _DOC_ID
    assert params["entity_type"] == "LOC"
    assert params["after_confidence"] is None
    assert conn.calls[1][1]["after_confidence"] == 0.25
    assert conn.calls[1][1]["after_id"] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert first[0]["cursor"] == second[0]["cursor"]


def test_uncertain_mentions_graphql_delegates_through_callers_connection(monkeypatch):
    from doc_ray.entrypoints.api import mention_confidence_query

    calls = []

    async def read(conn, document_id, *, entity_type, limit, after):
        calls.append((conn, document_id, entity_type, limit, after))
        return [
            {
                "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "document_id": document_id,
                "char_start": 10,
                "char_end": 17,
                "surface": "Arrakis",
                "entity_type": "LOC",
                "confidence": 0.25,
                "confidence_reasons": ["rare normalized surface"],
                "confidence_method": "heuristic:v1",
                "cursor": "next",
            }
        ]

    monkeypatch.setattr(mention_confidence_query.confidence_read, "uncertain_mentions", read)
    schema = build_schema(debug=True)

    async def execute():
        return await schema.execute(
            """
            query($id: ID!) {
              uncertainMentions(documentId: $id, limit: 500, entityType: "LOC") {
                id documentId charStart charEnd surface entityType
                confidence reasons method cursor
              }
            }
            """,
            variable_values={"id": _DOC_ID},
            context_value=_Context(conn="alice"),
        )

    result = asyncio.run(execute())
    assert result.errors is None, result.errors
    assert calls == [("alice", _DOC_ID, "LOC", 200, None)]
    assert result.data["uncertainMentions"][0]["reasons"] == ["rare normalized surface"]


def test_uncertain_mentions_malformed_document_id_returns_empty_without_read(monkeypatch):
    from doc_ray.entrypoints.api import mention_confidence_query

    async def fail(*args, **kwargs):
        raise AssertionError("malformed ids must not reach the database")

    monkeypatch.setattr(mention_confidence_query.confidence_read, "uncertain_mentions", fail)
    schema = build_schema(debug=True)

    async def execute():
        return await schema.execute(
            'query { uncertainMentions(documentId: "not-a-uuid") { id } }',
            context_value=_Context(conn="alice"),
        )

    result = asyncio.run(execute())
    assert result.errors is None
    assert result.data == {"uncertainMentions": []}


def test_mcp_uncertain_mentions_forwards_filters_and_explains_triage(monkeypatch):
    from doc_ray.entrypoints.mcp import mention_confidence

    calls = []

    async def gql(document, variables):
        calls.append((document, variables))
        return {"uncertainMentions": [{"surface": "Arrakis", "confidence": 0.25}]}

    monkeypatch.setattr(mention_confidence, "gql", gql)
    result = asyncio.run(
        mention_confidence.uncertain_mentions(_DOC_ID, limit=12, entity_type="LOC", after="next")
    )

    assert result == [{"surface": "Arrakis", "confidence": 0.25}]
    assert calls[0][1] == {"d": _DOC_ID, "n": 12, "e": "LOC", "a": "next"}
    docstring = " ".join((mention_confidence.uncertain_mentions.__doc__ or "").lower().split())
    assert "not a calibrated model probability" in docstring
    assert "correct_entity_mention" in docstring


def test_mcp_uncertain_mentions_rejects_a_malformed_document_id():
    from doc_ray.entrypoints.mcp import mention_confidence

    with pytest.raises(Exception, match="document_id"):
        asyncio.run(mention_confidence.uncertain_mentions("not-a-uuid"))
