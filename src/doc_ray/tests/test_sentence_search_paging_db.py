"""Routing search through the indexed helper must not move a page (BL-187, #51).

`docray_readable_sentence_ids` applies its ``LIMIT`` before any ordering, and
both search surfaces order by ``(document title, sentence ordinal, id)`` with
keyset pagination. That is the interaction #51 left open: an arbitrary capped
subset, sorted afterwards, is a *different page* than the sorted whole — and
nothing about the row shape reveals it, so a unit test over a fake connection
cannot see it either. These assertions run the real query against Postgres and
compare the routed pages to the pages the pre-existing predicate produces.

The comparison is the whole point. Every test here pages the same corpus twice
and demands the two agree row for row, across page boundaries, for a term whose
match count sits either side of the completeness cap.
"""

from __future__ import annotations

import asyncio
import uuid

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row

from doc_ray.adapters import persistence_read as read
from doc_ray.adapters import persistence_sentence_search as search

#: Rare on purpose. The completeness gate only helps a term whose whole match
#: set fits under the cap, which is the class D-035 measured as the expensive
#: one — a scan must read the entire relation to prove that few rows match.
RARE = "quokkazephyr"
MATCHING_SENTENCES = 7


@pytest.fixture
def corpus():
    """Two principals, each owning a document that contains the rare term."""
    conn = _itdb.connect()
    made: dict[str, list[str]] = {"documents": [], "principals": []}

    def principal() -> str:
        pid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO principals (id, kind, display_name) VALUES (%s,'service',%s)",
            (pid, f"page-{pid[:8]}"),
        )
        made["principals"].append(pid)
        return pid

    def document(title: str, owner: str, sentences: list[str]) -> str:
        # Seeded through the shared helper rather than by hand: phrase mode
        # cites from ``document_content`` via each sentence's span, so a
        # hand-rolled document with a placeholder span silently finds nothing
        # and would make these assertions vacuous rather than failing loudly.
        doc = _itdb.make_doc(conn, " ".join(f"{text}." for text in sentences))
        made["documents"].append(doc)
        conn.execute(
            "UPDATE documents SET title = %s, owner_principal_id = %s, visibility = 'private' "
            "WHERE id = %s",
            (title, owner, doc),
        )
        return str(doc)

    mine, theirs = principal(), principal()
    # Capitalised on purpose: the sentence splitter needs a real boundary, and a
    # lowercase continuation collapses the whole document into one sentence —
    # which reads as "the search found less than it should" rather than as the
    # seeding mistake it is.
    #
    # Two documents, so the ordering under test spans a title boundary rather
    # than reducing to "ordinal ascending within one document".
    document(
        "aaa-page-first",
        mine,
        [f"Sentence {i} mentions {RARE} once" for i in range(MATCHING_SENTENCES)],
    )
    document("bbb-page-second", mine, [f"A later {RARE} appears here", "No match in this one"])
    document("zzz-page-other", theirs, [f"A stranger's {RARE} must stay hidden"])
    try:
        yield {"mine": mine, "theirs": theirs}
    finally:
        for doc in made["documents"]:
            conn.execute("DELETE FROM documents WHERE id = %s", (doc,))
        for pid in made["principals"]:
            conn.execute("DELETE FROM principal_roles WHERE principal_id = %s", (pid,))
            conn.execute("DELETE FROM principals WHERE id = %s", (pid,))
        conn.close()


async def _aconn(principal_id: str) -> psycopg.AsyncConnection:
    conn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    await conn.execute("SET ROLE docray_api")
    await conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
    return conn


def _all_pages(principal_id: str, term: str, *, match: str = "substring", size: int = 2):
    """Walk every page, returning the rows in the order the surface emitted them."""

    async def go() -> list[dict]:
        conn = await _aconn(principal_id)
        try:
            collected: list[dict] = []
            after: str | None = None
            for _guard in range(50):  # a runaway cursor is a failure, not a hang
                page = await read.search_sentences(conn, term, match=match, limit=size, after=after)
                if not page:
                    return collected
                collected.extend(page)
                after = page[-1]["cursor"]
            raise AssertionError("pagination did not terminate")
        finally:
            await conn.close()

    return asyncio.run(go())


def _identity(rows: list[dict]) -> list[tuple]:
    """The part of a row a caller can observe and page on."""
    return [(r["title"], r["ordinal"], str(r["id"]), r["hit"]) for r in rows]


@pytest.mark.parametrize("match", ["substring", "phrase"])
def test_the_routed_pages_are_the_pages_the_old_predicate_produced(corpus, monkeypatch, match):
    """The contract #51 deferred: same rows, same order, across page boundaries."""
    routed = _all_pages(corpus["mine"], RARE, match=match)

    # A cap below the match count forces the fallback, which is the exact code
    # path that shipped before this change.
    monkeypatch.setattr(search, "PHRASE_CANDIDATE_SCAN_CAP", 2)
    fallback = _all_pages(corpus["mine"], RARE, match=match)

    assert _identity(routed) == _identity(fallback)
    assert len(routed) == MATCHING_SENTENCES + 1  # both documents, not just the first


@pytest.mark.parametrize("match", ["substring", "phrase"])
def test_paging_is_stable_at_a_size_that_does_not_divide_the_result(corpus, match):
    """A page size that straddles the document boundary must not repeat or skip."""
    by_two = _identity(_all_pages(corpus["mine"], RARE, match=match, size=2))
    by_three = _identity(_all_pages(corpus["mine"], RARE, match=match, size=3))
    in_one = _identity(_all_pages(corpus["mine"], RARE, match=match, size=100))

    assert by_two == by_three == in_one
    assert len({row[2] for row in by_two}) == len(by_two), "an id was emitted twice"


@pytest.mark.parametrize("match", ["substring", "phrase"])
def test_a_stranger_still_sees_nothing_through_the_routed_path(corpus, match):
    """The helper is SECURITY DEFINER: RLS is no longer the filter that stops this.

    ``test_sentence_search_definer_db.py`` proves the function itself scopes to
    the caller. This proves the *search surface* still does, now that it is the
    caller — the same corpus, the same term, a principal who owns none of it.
    """
    theirs = _all_pages(corpus["theirs"], RARE, match=match)

    assert [row["title"] for row in theirs] == ["zzz-page-other"]
    assert all("stranger" in row["text"] for row in theirs)


def test_a_miss_falls_back_rather_than_restricting_to_nothing(corpus):
    """An empty probe is ambiguous, so it must never become an empty answer.

    "No sentence matches" and "this connection resolves no readable documents"
    look identical from here, and the second is what an RLS-exempt caller
    produces — one that the old predicate showed every row. Restricting on an
    empty probe would silently invert that. The cost is a wasted probe on a
    genuine miss, which is the cheap case regardless.
    """

    async def go() -> list[str] | None:
        conn = await _aconn(corpus["mine"])
        try:
            return await search.complete_candidate_ids(
                conn, "zzzznosuchtermzzzz", None, cap=search.PHRASE_CANDIDATE_SCAN_CAP
            )
        finally:
            await conn.close()

    assert asyncio.run(go()) is None


def test_a_connection_with_no_principal_still_sees_what_it_always_saw(corpus):
    """The regression this fallback exists to prevent, proven end to end.

    An owner-role connection is exempt from RLS and reads the whole table under
    the old predicate, but resolves no principal — so the definer helper, which
    filters explicitly, offers it nothing. Routing on that would hand a
    privileged caller an empty page.
    """

    async def go() -> list[dict]:
        conn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            return await read.search_sentences(conn, RARE, limit=100)
        finally:
            await conn.close()

    assert len(asyncio.run(go())) == MATCHING_SENTENCES + 2  # both mine, plus the stranger's


def test_a_term_over_the_cap_reports_itself_as_unusable(corpus, monkeypatch):
    """Truncation must be indistinguishable from "cannot use the index" to callers."""

    async def go() -> list[str] | None:
        conn = await _aconn(corpus["mine"])
        try:
            return await search.complete_candidate_ids(conn, RARE, None, cap=2)
        finally:
            await conn.close()

    assert asyncio.run(go()) is None
