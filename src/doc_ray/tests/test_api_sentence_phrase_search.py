"""GraphQL integration for substring compatibility and phrase citations (#45)."""

from __future__ import annotations

import asyncio

import psycopg
from psycopg.rows import dict_row

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import ANONYMOUS


def test_graphql_sentence_search_exposes_both_match_modes_end_to_end(pg):
    import _itdb

    conn, created = pg
    text = (
        "Tangram ngram ungrammatical. "
        "The algorithm designed it. "
        "Algorithm Design and algorithm  design."
    )
    doc_id = _itdb.make_doc(conn, text)
    created.append(doc_id)
    schema = build_schema()

    async def check() -> None:
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            substring = await schema.execute(
                'query($id:ID!){searchSentences(term:"ngram",documentId:$id)'
                "{ordinal hit occurrenceCount}}",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert substring.errors is None, substring.errors
            assert substring.data["searchSentences"] == [
                {"ordinal": 0, "hit": "ngram", "occurrenceCount": 0}
            ]

            phrase = await schema.execute(
                'query($id:ID!){searchSentences(term:"algorithm design",documentId:$id,'
                "match:PHRASE){ordinal hit occurrences{charStart charEnd}}}",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert phrase.errors is None, phrase.errors
            hits = phrase.data["searchSentences"]
            assert [hit["ordinal"] for hit in hits] == [2]
            assert hits[0]["hit"] == "Algorithm Design"
            for occurrence in hits[0]["occurrences"]:
                cited = text[occurrence["charStart"] : occurrence["charEnd"]]
                assert cited in {"Algorithm Design", "algorithm  design"}

            page = await schema.execute(
                'query($id:ID!){searchSentencePage(term:"algorithm design",documentId:$id,'
                "match:PHRASE){matchProfile candidateLimitReached hits{ordinal}}}",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert page.errors is None, page.errors
            assert page.data["searchSentencePage"] == {
                "matchProfile": "surface_word_v1",
                "candidateLimitReached": False,
                "hits": [{"ordinal": 2}],
            }
        finally:
            await aconn.close()

    asyncio.run(check())
