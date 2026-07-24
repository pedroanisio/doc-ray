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


def test_corpus_shard_partition_is_complete_disjoint_and_deterministic(pg):
    conn, created = pg
    import _itdb

    ids = [_itdb.make_doc(conn, _TEXT) for _ in range(6)]
    created.extend(ids)
    k = 3

    async def check():
        aconn = await _aconn()
        try:
            # Collect every shard; a document must appear in exactly one shard.
            placement: dict[str, int] = {}
            for shard in range(k):
                rows = await read.list_documents(
                    aconn, limit=10_000, shard_index=shard, shard_count=k
                )
                for r in rows:
                    doc = str(r["id"])
                    assert doc not in placement, "a document appeared in two shards"
                    placement[doc] = shard

            # Every seeded document is placed, in the shard its hash predicts.
            for doc in ids:
                assert doc in placement, "a document fell into no shard (partition incomplete)"
                expected = conn.execute(
                    "SELECT ((hashtext(%s::text) %% %s) + %s) %% %s AS s", (doc, k, k, k)
                ).fetchone()["s"]
                assert placement[doc] == expected

            # Determinism: re-reading a shard yields the same membership.
            again = {
                str(r["id"])
                for r in await read.list_documents(
                    aconn, limit=10_000, shard_index=0, shard_count=k
                )
            }
            first = {d for d, s in placement.items() if s == 0}
            assert again == first

            # Manifest: one entry per shard 0..k-1; counts sum to the visible total.
            counts = await read.corpus_shard_counts(aconn, k)
            assert [r["shard"] for r in counts] == list(range(k))
            assert sum(r["n"] for r in counts) == len(placement)
            # each seeded doc is counted in its own shard
            for doc in ids:
                assert counts[placement[doc]]["n"] >= 1
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


def test_fulltext_search_matches_each_documents_ts_config(pg):
    """The tsquery must use each document's own config, not hardcoded 'english'.

    An unannotated document (``language IS NULL``) has a 'simple'-config fts
    vector (migration 016); an English-stemmed query like ``florqins`` ->
    ``florqin`` finds no lexeme in it unless the reader mirrors the writer's
    config choice. An annotated (``language = 'en'``) document must keep real
    English stemming (singular query hits plural text).

    Uses an invented word (Postgres's English stemmer is the algorithmic
    Snowball stripper, not a dictionary, so it stems nonsense words exactly
    like real ones) so the fixture documents are the query's *only* possible
    hits — a real word would drift out of a rank-limited result page as the
    shared corpus grows, decoupling this test from corpus size entirely.
    """
    import _itdb

    conn, created = pg
    text = "Polytechnic florqins teach applied design."
    doc_null = _itdb.make_doc(conn, text)
    created.append(doc_null)
    doc_en = _itdb.make_doc(conn, text)
    created.append(doc_en)
    conn.execute("UPDATE documents SET language = 'en' WHERE id = %s", (doc_en,))

    async def check():
        aconn = await _aconn()
        try:
            plural = await read.fulltext_search(aconn, "florqins")
            assert any(str(r["doc_id"]) == str(doc_null) for r in plural)

            singular = await read.fulltext_search(aconn, "florqin")
            assert any(str(r["doc_id"]) == str(doc_en) for r in singular)
        finally:
            await aconn.close()

    asyncio.run(check())
