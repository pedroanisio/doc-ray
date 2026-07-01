"""Integration tests for the async read adapter (skip without a live DB).

Seeds fixtures with the sync helpers (``_itdb``) and reads them back through the
async adapter, proving the sync-writer / async-reader split works against one DB.
Uses ``asyncio.run`` so no pytest-asyncio plugin is required at collection time.
"""

from __future__ import annotations

import asyncio

import psycopg
from psycopg.rows import dict_row

from doc_ray.adapters import persistence_read as read

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


def test_document_reads(pg):
    doc_id = _seed(pg)

    async def check():
        aconn = await _aconn()
        try:
            doc = await read.get_document(aconn, doc_id)
            assert doc is not None
            assert doc["title"] == "itest"
            assert isinstance(doc["authors"], list)
            assert isinstance(doc["tags"], list)
            assert isinstance(doc["identifiers"], list)
            assert isinstance(doc["metadata"], dict)

            assert await read.get_document(aconn, "00000000-0000-0000-0000-000000000000") is None

            sents = await read.list_sentences(aconn, doc_id)
            assert len(sents) == 2
            assert sents[0]["ordinal"] == 0
            assert sents[0]["char_start"] < sents[0]["char_end"]

            limited = await read.list_sentences(aconn, doc_id, limit=1)
            assert len(limited) == 1

            prov = await read.get_provenance(aconn, doc_id)
            assert isinstance(prov, list)
            assert all("params" in e for e in prov)

            summaries = await read.list_documents(aconn, limit=50)
            mine = [s for s in summaries if str(s["id"]) == str(doc_id)]
            assert mine and mine[0]["n_sentences"] == 2
        finally:
            await aconn.close()

    asyncio.run(check())


def test_search_reads(pg):
    doc_id = _seed(pg)

    async def check():
        aconn = await _aconn()
        try:
            hits = await read.search_sentences(aconn, "startup")
            assert any(str(h["doc_id"]) == str(doc_id) for h in hits)
            hit = next(h for h in hits if str(h["doc_id"]) == str(doc_id))
            assert hit["hit"] == "startup"
            assert "startup" not in hit["pre"]

            fts = await read.fulltext_search(aconn, "startup")
            assert any(str(r["doc_id"]) == str(doc_id) for r in fts)

            assert await read.get_lexeme(aconn, "lx_none.x_deadbeef") is None
            assert isinstance(await read.list_lexemes(aconn, limit=1), list)
            assert isinstance(await read.lexeme_kwic(aconn, "lx_none.x_deadbeef"), list)
        finally:
            await aconn.close()

    asyncio.run(check())
