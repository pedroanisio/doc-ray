"""Routing sentence search through the indexed candidate helper (BL-187, #51).

`term_frequency` reached the trigram index in #51; the two *search* paths did
not, because they order by ``(document title, sentence ordinal, id)`` with
keyset pagination and `docray_readable_sentence_ids` applies its ``LIMIT``
*before* any ordering. Capping an unordered set and then sorting the survivors
returns a different page than sorting the whole match set and taking the first
one — silently, and only for the queries that matched a lot.

The resolution is **completeness gating**: ask the helper for ``cap + 1`` rows.

* ``<= cap`` rows come back — the helper enumerated the *complete* match set, the
  cap never bit, and ordering that set outside is identical to ordering the
  table. The page is exact and the index did the work.
* ``cap + 1`` rows come back — the set was truncated, ordering it would be
  arbitrary, and the query falls back to the pre-existing predicate unchanged.

This puts the index precisely where the measurement said the pain was. D-035
records that the *rare* phrase is the expensive case — the scan must read the
whole relation to prove few rows match (``Model Context Protocol``: 1,974,077
rows removed by filter, 5,167 ms, to return 358) — and those are exactly the
queries whose complete match set fits under a cap. The non-selective classes
that truncate are already the cheap case (``of it``: 54 ms p50), because a
non-selective predicate fills the cap immediately and ``LIMIT`` stops the scan.

The helper's patterns are a *superset* of the original predicate: it splits a
multi-word term into words and drops those that are short, confusable or
fold-matched. So the original predicate stays in the outer query. On the phrase
path that keeps the candidate definition unchanged before ``surface_word_v1``
verification; on the substring path it is load-bearing, because nothing else
verifies there.
"""

from __future__ import annotations

from typing import Any

import pytest

from doc_ray.adapters import persistence_read as read
from doc_ray.adapters import persistence_sentence_search as search
from doc_ray.adapters.sentence_search_sql import HELPER_FUNCTION

DOC_ID = "11111111-1111-1111-1111-111111111111"


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Conn:
    """Records every statement, and answers them from a queue in order."""

    def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
        self.batches = list(batches)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, sql: str, params: dict[str, Any]) -> _Cursor:
        self.calls.append((sql, params))
        return _Cursor(self.batches.pop(0) if self.batches else [])

    @property
    def probes(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if HELPER_FUNCTION in call[0]]

    @property
    def pages(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if HELPER_FUNCTION not in call[0]]


def _ulid(suffix: int) -> str:
    return f"01ARZ3NDEKTSV4RRFFQ69G5{suffix:03d}"


def _candidate_id(suffix: int) -> dict[str, Any]:
    return {"id": _ulid(suffix)}


def _row(suffix: int, text: str, *, ordinal: int, start: int = 0) -> dict[str, Any]:
    return {
        "id": _ulid(suffix),
        "doc_id": DOC_ID,
        "title": "Algorithms",
        "ordinal": ordinal,
        "text": text,
        "char_start": start,
    }


# --------------------------------------------------------------------------
# substring path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_substring_search_probes_the_helper_for_a_complete_candidate_set():
    """An indexable term asks the helper for one row more than the cap."""
    conn = _Conn([[_candidate_id(1)], [_row(1, "the ngram is here", ordinal=3)]])

    await read.search_sentences(conn, "ngram", limit=10)

    assert len(conn.probes) == 1
    probe_sql, probe_params = conn.probes[0]
    assert HELPER_FUNCTION in probe_sql
    # cap + 1 is the completeness signal: cap rows could always mean "there were
    # more", so the extra row is what distinguishes complete from truncated.
    assert probe_params["cand_cap"] == search.PHRASE_CANDIDATE_SCAN_CAP + 1


@pytest.mark.asyncio
async def test_a_complete_candidate_set_pages_by_id_and_keeps_the_original_predicate():
    """The helper narrows; the original predicate and the ordering still decide."""
    conn = _Conn([[_candidate_id(1), _candidate_id(2)], [_row(1, "the ngram is here", ordinal=3)]])

    rows = await read.search_sentences(conn, "ngram", limit=10)

    page_sql, page_params = conn.pages[0]
    assert "cand_ids" in page_sql, "the page must be restricted to the candidate ids"
    assert page_params["cand_ids"] == [_ulid(1), _ulid(2)]
    # The helper's patterns are a superset, so the exact predicate must survive.
    assert "ILIKE" in page_sql
    assert "ORDER BY d.title, s.ordinal, s.id" in page_sql
    assert [row["ordinal"] for row in rows] == [3]


@pytest.mark.asyncio
async def test_a_truncated_candidate_set_falls_back_to_the_unindexed_page(monkeypatch):
    """Above the cap the helper's subset is arbitrary, so it must not be paged."""
    monkeypatch.setattr(search, "PHRASE_CANDIDATE_SCAN_CAP", 3)
    conn = _Conn(
        [
            [_candidate_id(i) for i in range(4)],  # cap + 1 -> truncated
            [_row(9, "the ngram is here", ordinal=3)],
        ]
    )

    rows = await read.search_sentences(conn, "ngram", limit=10)

    page_sql, page_params = conn.pages[0]
    assert "cand_ids" not in page_sql, "a truncated probe must not restrict the page"
    assert "cand_ids" not in page_params
    assert "ORDER BY d.title, s.ordinal, s.id" in page_sql
    assert [row["ordinal"] for row in rows] == [3]


@pytest.mark.asyncio
async def test_a_term_that_cannot_drive_the_index_is_never_probed():
    """Under three characters yields no trigram; probing would only cost a round trip."""
    conn = _Conn([[_row(1, "of it", ordinal=1)]])

    await read.search_sentences(conn, "of", limit=10)

    assert conn.probes == []
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_the_document_filter_reaches_the_probe():
    """A single-document search must narrow the helper too, not only the page."""
    conn = _Conn([[_candidate_id(1)], [_row(1, "the ngram is here", ordinal=3)]])

    await read.search_sentences(conn, "ngram", document_id=DOC_ID, limit=10)

    assert conn.probes[0][1]["cand_doc"] == DOC_ID


@pytest.mark.asyncio
async def test_the_routed_page_matches_the_fallback_page_row_for_row(monkeypatch):
    """The contract that matters: routing changes the plan, never the answer."""
    page = [_row(1, "the ngram is here", ordinal=3), _row(2, "another ngram", ordinal=7)]

    routed = _Conn([[_candidate_id(1), _candidate_id(2)], list(page)])
    rows_routed = await read.search_sentences(routed, "ngram", limit=10)

    monkeypatch.setattr(search, "PHRASE_CANDIDATE_SCAN_CAP", 1)
    fell_back = _Conn([[_candidate_id(1), _candidate_id(2)], list(page)])
    rows_fallback = await read.search_sentences(fell_back, "ngram", limit=10)

    assert routed.pages[0][0] != fell_back.pages[0][0], "the two plans must differ"
    assert rows_routed == rows_fallback


# --------------------------------------------------------------------------
# phrase path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phrase_search_probes_once_and_restricts_every_batch():
    """The probe is per search, not per batch: the candidate set does not move."""
    conn = _Conn(
        [
            [_candidate_id(1), _candidate_id(2)],
            [_row(1, "Algorithm Design", ordinal=1), _row(2, "Algorithm  Design", ordinal=2)],
        ]
    )

    rows = await read.search_sentences(conn, "algorithm design", match="phrase", limit=10)

    assert len(conn.probes) == 1
    for _sql, params in conn.pages:
        assert params["cand_ids"] == [_ulid(1), _ulid(2)]
    assert [row["ordinal"] for row in rows] == [1, 2]


@pytest.mark.asyncio
async def test_phrase_search_keeps_its_keyset_batching_when_routed(monkeypatch):
    """Restricting by id must not disturb the resumable batch cursor."""
    monkeypatch.setattr(search, "PHRASE_CANDIDATE_BATCH_SIZE", 2)
    conn = _Conn(
        [
            [_candidate_id(i) for i in range(1, 4)],
            [_row(1, "algorithm designed", ordinal=0), _row(2, "design algorithm", ordinal=1)],
            [_row(3, "Algorithm Design", ordinal=2)],
        ]
    )

    rows = await read.search_sentences(conn, "algorithm design", match="phrase", limit=2)

    assert [row["ordinal"] for row in rows] == [2]
    assert conn.pages[1][1]["after_ord"] == 1
    assert rows.candidate_scanned == 3


@pytest.mark.asyncio
async def test_phrase_search_falls_back_whole_when_the_probe_truncates(monkeypatch):
    """Truncation is decided once, for the search, not per batch."""
    monkeypatch.setattr(search, "PHRASE_CANDIDATE_SCAN_CAP", 2)
    conn = _Conn(
        [
            [_candidate_id(i) for i in range(3)],  # cap + 1
            [_row(1, "Algorithm Design", ordinal=1)],
        ]
    )

    rows = await read.search_sentences(conn, "algorithm design", match="phrase", limit=10)

    for sql, params in conn.pages:
        assert "cand_ids" not in sql
        assert "cand_ids" not in params
    assert [row["ordinal"] for row in rows] == [1]


@pytest.mark.asyncio
async def test_an_all_short_phrase_is_never_probed():
    """`of it` has no word a trigram index can hold; it is already the cheap case."""
    conn = _Conn([[_row(1, "of it", ordinal=1)]])

    await read.search_sentences(conn, "of it", match="phrase", limit=10)

    assert conn.probes == []
