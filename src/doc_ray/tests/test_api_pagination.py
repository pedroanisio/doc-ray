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


def test_documents_filters_by_doc_type_status_and_tags(pg):
    """documents(docType:, status:, tags:) narrows the result set (BL-063)."""
    import _itdb

    from doc_ray.adapters import persistence as db

    conn, created = pg
    pdf_id = db.insert_document(
        conn,
        title="pdf-one",
        doc_type="pdf",
        source_uri="s3://it",
        content_sha256=_itdb.unique_sha(),
        byte_size=10,
        page_count=1,
        full_text="x",
        status="extracted",
        tags=["reviewed"],
    )
    created.append(pdf_id)
    txt_id = _itdb.make_doc(conn, "Some text.")
    created.append(txt_id)
    schema = build_schema()

    async def check() -> None:
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))

            by_type = await schema.execute(
                'query{ documents(docType:"pdf", limit: 200){ id docType } }', context_value=ctx
            )
            assert by_type.errors is None, by_type.errors
            ids = {d["id"] for d in by_type.data["documents"]}
            assert str(pdf_id) in ids
            assert str(txt_id) not in ids

            by_tag = await schema.execute(
                'query{ documents(tags: ["reviewed"], limit: 200){ id } }', context_value=ctx
            )
            assert by_tag.errors is None, by_tag.errors
            tag_ids = {d["id"] for d in by_tag.data["documents"]}
            assert str(pdf_id) in tag_ids
            assert str(txt_id) not in tag_ids

            by_status = await schema.execute(
                'query{ documents(status:"extracted", limit: 200){ id } }', context_value=ctx
            )
            assert by_status.errors is None, by_status.errors
            status_ids = {d["id"] for d in by_status.data["documents"]}
            assert str(pdf_id) in status_ids and str(txt_id) in status_ids
        finally:
            await aconn.close()

    asyncio.run(check())


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
