"""GraphQL API tests: schema-level execution against the read adapter.

The depth-limit test runs anywhere (it's a validation rule, no DB needed). The
query tests seed with the sync helpers and execute the schema against an async
connection, skipping cleanly when Postgres is unreachable.
"""

from __future__ import annotations

import asyncio

import psycopg
from psycopg.rows import dict_row

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import ANONYMOUS

_TEXT = "Elena founded a startup in Berlin. It grew fast."


async def _aconn() -> psycopg.AsyncConnection:
    import _itdb

    return await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )


def _seed(pg) -> str:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, _TEXT)
    created.append(doc_id)
    return doc_id


def test_depth_limit_rejects_deep_queries():
    # Validation-time rule: no DB or context needed — the query never executes.
    schema = build_schema(max_depth=1)

    async def check():
        # document { sentences { ordinal } } nests two object levels (depth 2 > 1).
        result = await schema.execute(
            '{ document(id: "x") { sentences { ordinal } } }', context_value=None
        )
        assert result.errors, "expected the depth limiter to reject a depth-2 query"
        assert "depth" in str(result.errors[0]).lower()

    asyncio.run(check())


def test_documents_and_document(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            listing = await schema.execute(
                "{ documents(limit: 50) { id title sentenceCount authors } }",
                context_value=ctx,
            )
            assert listing.errors is None, listing.errors
            mine = [d for d in listing.data["documents"] if d["id"] == str(doc_id)]
            assert mine and mine[0]["sentenceCount"] == 2
            assert isinstance(mine[0]["authors"], list)

            detail = await schema.execute(
                "query ($id: ID!) { document(id: $id) "
                "{ title status sentences(limit: 5) { ordinal text } } }",
                variable_values={"id": str(doc_id)},
                context_value=ctx,
            )
            assert detail.errors is None, detail.errors
            assert detail.data["document"]["title"] == "itest"
            assert len(detail.data["document"]["sentences"]) == 2

            missing = await schema.execute(
                '{ document(id: "00000000-0000-0000-0000-000000000000") { title } }',
                context_value=ctx,
            )
            assert missing.errors is None, missing.errors
            assert missing.data["document"] is None
        finally:
            await aconn.close()

    asyncio.run(check())


def test_search_and_lexeme(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn()
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            search = await schema.execute(
                '{ searchSentences(term: "startup") { documentId hit } }',
                context_value=ctx,
            )
            assert search.errors is None, search.errors
            assert any(
                h["documentId"] == str(doc_id) and h["hit"] == "startup"
                for h in search.data["searchSentences"]
            )

            lexeme = await schema.execute(
                '{ lexeme(uid: "lx_none.x_deadbeef") { lemma } }', context_value=ctx
            )
            assert lexeme.errors is None, lexeme.errors
            assert lexeme.data["lexeme"] is None
        finally:
            await aconn.close()

    asyncio.run(check())
