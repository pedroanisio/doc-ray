"""GraphQL keyset-pagination contract tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import ANONYMOUS


def test_graphql_lists_expose_cursor_pagination_not_offset():
    """GraphQL list fields use keyset cursors instead of offset arguments."""
    from doc_ray.entrypoints.api import query as query_mod
    from doc_ray.entrypoints.api import types as types_mod

    sdl = build_schema().as_str()
    for field in (
        "documents(",
        "searchSentences(",
        "lexemes(",
        "entities(",
        "entityFrequency(",
        "jobs(",
        "sentences(",
        "occurrences(",
    ):
        start = sdl.index(field)
        signature = sdl[start : sdl.index("):", start)]
        assert "after:" in signature or field == "jobs("
        assert "offset:" not in signature

    for source in (Path(query_mod.__file__).read_text(), Path(types_mod.__file__).read_text()):
        assert "offset: int" not in source
        assert "offset=" not in source


def test_search_sentences_pages_after_returned_cursor(pg):
    """A caller can request the next search page using the prior row's cursor."""
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Winston waited. Winston wrote. Winston walked.")
    created.append(doc_id)
    schema = build_schema()

    async def check() -> None:
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            first = await schema.execute(
                'query($id:ID!){ searchSentences(term:"Winston", documentId:$id, limit:1) '
                "{ ordinal cursor } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert first.errors is None, first.errors
            first_hit = first.data["searchSentences"][0]

            second = await schema.execute(
                'query($id:ID!,$after:String!){ searchSentences(term:"Winston", '
                "documentId:$id, limit:1, after:$after) { ordinal cursor } }",
                variable_values={"id": str(doc_id), "after": first_hit["cursor"]},
                context_value=ctx,
            )
            assert second.errors is None, second.errors
            assert second.data["searchSentences"][0]["ordinal"] == first_hit["ordinal"] + 1
        finally:
            await aconn.close()

    asyncio.run(check())
