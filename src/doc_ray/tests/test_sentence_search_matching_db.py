"""The literal-substring contract, proven against Postgres (skip without a DB).

``domain.search_terms`` decides the matching rule and ``test_search_terms.py``
proves the decision; this proves the *rendered SQL* keeps it — that the escaped
patterns, the OR expansion and the ``translate()`` fallback all execute and
select the rows the rule says they should. A pure-logic test cannot catch a
predicate that is well-formed Python and malformed SQL.

Each case is a false answer the search surface used to give:

* ``Hick's`` found nothing in a corpus that stores ``Hick’s`` — and the zero was
  reported as an exhaustive scan, i.e. as evidence of absence.
* ``shared_buffers`` matched ``sharedXbuffers``, because ``_`` reached Postgres
  as a wildcard on the exact term class BL-164 routes to this tool.
* A hit found case-insensitively came back with an empty KWIC ``hit`` window.
"""

from __future__ import annotations

import asyncio

import psycopg
from psycopg.rows import dict_row

from doc_ray.adapters import persistence_read as read

_TEXT = (
    "Hick’s law bounds decision time. "
    "The thick central line is unrelated. "
    "Tuning shared_buffers matters, but sharedXbuffers does not. "
    "A 100% increase — see the “scale” note — was recorded."
)


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


def _run(doc_id: str, term: str) -> list[dict]:
    async def go() -> list[dict]:
        aconn = await _aconn()
        try:
            return await read.search_sentences(aconn, term, document_id=doc_id)
        finally:
            await aconn.close()

    return asyncio.run(go())


def test_an_ascii_apostrophe_finds_the_typographic_one(pg):
    doc_id = _seed(pg)

    hits = _run(doc_id, "Hick's law")

    assert [h["ordinal"] for h in hits] == [0]
    # The window quotes the DOCUMENT's spelling, not the caller's.
    assert hits[0]["hit"] == "Hick’s law"


def test_the_typographic_apostrophe_still_finds_itself(pg):
    doc_id = _seed(pg)

    assert [h["ordinal"] for h in _run(doc_id, "Hick’s law")] == [0]


def test_an_underscore_is_data_not_a_wildcard(pg):
    doc_id = _seed(pg)

    hits = _run(doc_id, "shared_buffers")

    assert len(hits) == 1
    assert "shared_buffers" in hits[0]["text"]
    assert hits[0]["hit"] == "shared_buffers"  # not the sharedXbuffers decoy


def test_a_percent_sign_is_data_not_a_wildcard(pg):
    doc_id = _seed(pg)

    # As a wildcard, '%100%%' would match every sentence in the document.
    hits = _run(doc_id, "100%")

    assert len(hits) == 1
    assert hits[0]["hit"] == "100%"


def test_a_case_insensitive_hit_still_gets_a_populated_kwic_window(pg):
    doc_id = _seed(pg)

    hits = _run(doc_id, "hick’s")

    assert hits and hits[0]["hit"] == "Hick’s"
    assert hits[0]["pre"] == ""
    assert "law" in hits[0]["post"]


def test_the_substring_rule_is_unchanged_for_a_short_term(pg):
    # Folding widens spellings; it must not quietly add word-boundary semantics.
    # 'hick' still matches 'thick' — that is the documented substring rule, and
    # the tool description now says so up front instead of letting the caller
    # infer lemma normalization from the result.
    doc_id = _seed(pg)

    ordinals = {h["ordinal"] for h in _run(doc_id, "hick")}

    assert {0, 1} <= ordinals


def test_a_term_too_confusable_to_enumerate_still_matches_via_the_folded_column(pg):
    doc_id = _seed(pg)

    # Four confusables (— “ ” plus the quote pair) overflow MAX_PATTERNS, so this
    # takes the translate() plan; the answer must be identical.
    hits = _run(doc_id, '- see the "scale" note -')

    assert [h["ordinal"] for h in hits] == [3]
    assert hits[0]["hit"] == "— see the “scale” note —"
