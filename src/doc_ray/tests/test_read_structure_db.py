"""Integration tests for the structure read adapter (skip without a live DB).

The pure logic is covered by test_document_outline.py / test_entity_profiles.py.
What can only be proven against a real database is the part those cannot see: the
SQL that recovers a heading's absolute character offset from the stored text, the
lateral that resolves that offset onto the sentence containing it, and the
fallback taken when the ``document_stats`` rollup has not been refreshed yet.

Seeds with the sync helpers (``_itdb``) and reads back through the async adapter,
the same split as test_persistence_read.py.
"""

from __future__ import annotations

import asyncio

import psycopg
from psycopg.rows import dict_row

from doc_ray.adapters import persistence_read_structure as structure_read

#: A document whose headings the extractors would have normalized to ATX form.
_WITH_HEADINGS = (
    "# Book One\n"
    "Paul walked the sand. The spice flowed around him.\n"
    "## Chapter Two\n"
    "Jessica watched from the ledge. She said nothing at all.\n"
    "### Chapter Three\n"
    "Duncan Idaho returned. The ghola remembered everything.\n"
)

#: A novel as the PDF extractor actually leaves it — no heading survived.
_NO_HEADINGS = (
    "Paul walked the sand. The spice flowed around him. "
    "Jessica watched from the ledge. She said nothing at all. "
    "Duncan Idaho returned. The ghola remembered everything."
)


async def _aconn() -> psycopg.AsyncConnection:
    import _itdb

    return await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )


def _seed(pg, text: str) -> str:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, text)
    created.append(doc_id)
    return doc_id


def test_headings_become_ordinal_ranges_that_cover_the_document(pg):
    doc_id = _seed(pg, _WITH_HEADINGS)

    async def check():
        aconn = await _aconn()
        try:
            outline = await structure_read.document_outline(aconn, doc_id)
            assert outline["source"] == "headings"
            entries = outline["entries"]
            assert [e["title"] for e in entries] == ["Book One", "Chapter Two", "Chapter Three"]
            assert [e["level"] for e in entries] == [1, 2, 3]

            # Contiguous, gapless, and reaching the last sentence — the property
            # that makes the outline safe to use as a coverage plan.
            assert entries[0]["ordinal_start"] == 0
            for previous, following in zip(entries, entries[1:], strict=False):
                assert following["ordinal_start"] == previous["ordinal_end"] + 1
            assert entries[-1]["ordinal_end"] == outline["total_sentences"] - 1

            # The offsets are real positions in the stored text, not indexes.
            assert entries[1]["char_start"] == _WITH_HEADINGS.index("## Chapter Two")
        finally:
            await aconn.close()

    asyncio.run(check())


def test_the_ranges_actually_address_the_right_sentences(pg):
    # The join under test is offset -> containing sentence. If it is off by one
    # sentence the outline still *looks* well-formed, so read the slice back.
    from doc_ray.adapters import persistence_read as read

    doc_id = _seed(pg, _WITH_HEADINGS)

    async def check():
        aconn = await _aconn()
        try:
            outline = await structure_read.document_outline(aconn, doc_id)
            second = outline["entries"][1]
            sentences = await read.list_sentences(
                aconn,
                doc_id,
                from_ordinal=second["ordinal_start"],
                to_ordinal=second["ordinal_end"],
            )
            body = " ".join(s["text"] for s in sentences)
            assert "Jessica" in body
            assert "Duncan Idaho" not in body  # that belongs to the next entry
        finally:
            await aconn.close()

    asyncio.run(check())


def test_a_document_without_headings_falls_back_to_a_labelled_grid(pg):
    doc_id = _seed(pg, _NO_HEADINGS)

    async def check():
        aconn = await _aconn()
        try:
            outline = await structure_read.document_outline(aconn, doc_id, max_entries=3)
            assert outline["source"] == "uniform"
            assert outline["note"] and "not a table of contents" in outline["note"]
            entries = outline["entries"]
            assert 0 < len(entries) <= 3
            assert entries[0]["ordinal_start"] == 0
            assert entries[-1]["ordinal_end"] == outline["total_sentences"] - 1
            # Each bucket is labelled with the sentence it opens on, so the grid
            # is navigable rather than a row of anonymous numbers.
            assert entries[0]["title"].startswith("Paul walked")
        finally:
            await aconn.close()

    asyncio.run(check())


def test_the_outline_works_before_the_stats_rollup_has_been_refreshed(pg):
    # document_stats is refreshed by the annotation job handlers, so a
    # freshly-extracted document carries a zero rollup while its sentences
    # already exist. Reporting "no sentences yet" there would be wrong.
    conn, _ = pg
    doc_id = _seed(pg, _NO_HEADINGS)
    conn.execute("DELETE FROM document_stats WHERE document_id = %s", (doc_id,))

    async def check():
        aconn = await _aconn()
        try:
            outline = await structure_read.document_outline(aconn, doc_id)
            assert outline["total_sentences"] > 0
            assert outline["source"] == "uniform"
            assert outline["entries"]
        finally:
            await aconn.close()

    asyncio.run(check())


def test_an_unknown_document_yields_an_empty_outline_not_an_error(pg):
    async def check():
        aconn = await _aconn()
        try:
            outline = await structure_read.document_outline(
                aconn, "00000000-0000-0000-0000-000000000000"
            )
            assert outline == {
                "source": "empty",
                "total_sentences": 0,
                "entry_count": 0,
                "note": "No such document, or it is not readable with this token.",
                "entries": [],
            }
        finally:
            await aconn.close()

    asyncio.run(check())


def test_max_entries_is_respected_and_clamped(pg):
    doc_id = _seed(pg, _NO_HEADINGS)

    async def check():
        aconn = await _aconn()
        try:
            assert (
                len(
                    (await structure_read.document_outline(aconn, doc_id, max_entries=2))["entries"]
                )
                <= 2
            )
            # A nonsense cap must not produce a nonsense outline.
            assert (await structure_read.document_outline(aconn, doc_id, max_entries=0))["entries"]
        finally:
            await aconn.close()

    asyncio.run(check())


def test_entity_profiles_read_end_to_end_and_report_unresolved_honestly(pg):
    doc_id = _seed(pg, _NO_HEADINGS)

    async def check():
        aconn = await _aconn()
        try:
            profiles = await structure_read.entity_profiles(aconn, document_id=doc_id, limit=10)
            # The fixture is not annotated, so there is nothing to reconcile —
            # the path must return cleanly rather than fail.
            assert isinstance(profiles, list)
            for profile in profiles:
                assert set(profile) >= {
                    "surface",
                    "mentions",
                    "dominant_type",
                    "type_spread",
                    "type_consistency",
                    "resolved",
                }
                # Nothing may claim registry backing without a canonical row.
                assert profile["resolved"] == (profile["canonical_id"] is not None)
        finally:
            await aconn.close()

    asyncio.run(check())
