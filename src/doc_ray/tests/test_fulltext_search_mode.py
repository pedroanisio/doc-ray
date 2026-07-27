"""Full-text search mode contracts for GitHub issue #40."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_ray.adapters import persistence_fulltext_read as read
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.mcp import corpus


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "parser"),
    [("terms", "plainto_tsquery"), ("phrase", "phraseto_tsquery")],
)
async def test_adapter_uses_the_requested_safe_tsquery_parser(monkeypatch, mode, parser):
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(read, "_rows", rows)

    await read.fulltext_search(object(), "design decision", mode=mode, limit=7)

    sql = rows.await_args.args[1]
    assert parser in sql
    assert rows.await_args.args[2] == ("design decision", 7)


@pytest.mark.asyncio
async def test_adapter_rejects_an_unknown_mode_before_querying(monkeypatch):
    rows = AsyncMock(return_value=[])
    monkeypatch.setattr(read, "_rows", rows)

    with pytest.raises(ValueError, match="terms.*phrase"):
        await read.fulltext_search(object(), "design decision", mode="raw")

    rows.assert_not_awaited()


def test_graphql_exposes_a_typed_mode_with_a_backwards_compatible_default():
    sdl = build_schema().as_str()

    assert "enum FullTextSearchMode" in sdl
    assert (
        "fullTextSearch(query: String!, limit: Int! = 25, mode: FullTextSearchMode! = TERMS)" in sdl
    )


@pytest.mark.asyncio
async def test_mcp_forwards_phrase_mode_as_the_graphql_enum(monkeypatch):
    gql = AsyncMock(return_value={"fullTextSearch": []})
    monkeypatch.setattr(corpus, "gql", gql)

    assert await corpus.full_text_search("design decision", mode="phrase", limit=4) == []

    query, variables = gql.await_args.args
    assert "$m:FullTextSearchMode!" in query
    assert "mode:$m" in query
    assert variables == {"q": "design decision", "l": 4, "m": "PHRASE"}


@pytest.mark.asyncio
async def test_mcp_rejects_an_unknown_mode_without_calling_graphql(monkeypatch):
    gql = AsyncMock()
    monkeypatch.setattr(corpus, "gql", gql)

    with pytest.raises(corpus.ToolCallError, match="terms.*phrase") as exc:
        await corpus.full_text_search("design decision", mode="raw")

    assert exc.value.kind == "bad_input"
    gql.assert_not_awaited()
