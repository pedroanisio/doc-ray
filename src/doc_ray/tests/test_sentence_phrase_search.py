"""Unit contracts for bounded, verified sentence phrase search (#45)."""

from __future__ import annotations

import pytest

from doc_ray.adapters import persistence_read as read


class _Cursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict]:
        return self._rows


class _Conn:
    def __init__(self, batches: list[list[dict]]) -> None:
        self.batches = list(batches)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, sql: str, params: dict) -> _Cursor:
        self.calls.append((sql, params))
        return _Cursor(self.batches.pop(0) if self.batches else [])


def _candidate(identifier: str, text: str, *, start: int, ordinal: int) -> dict:
    return {
        "id": identifier,
        "doc_id": "11111111-1111-1111-1111-111111111111",
        "title": "Algorithms",
        "ordinal": ordinal,
        "text": text,
        "char_start": start,
    }


@pytest.mark.asyncio
async def test_phrase_mode_verifies_candidates_and_cites_every_absolute_occurrence():
    conn = _Conn(
        [
            [
                _candidate("01ARZ3NDEKTSV4RRFFQ69G5FAA", "algorithm designed", start=20, ordinal=1),
                _candidate(
                    "01ARZ3NDEKTSV4RRFFQ69G5FAB",
                    "Algorithm  Design then algorithm-design.",
                    start=100,
                    ordinal=2,
                ),
            ]
        ]
    )

    rows = await read.search_sentences(conn, "algorithm design", match="phrase", limit=10)

    assert [row["ordinal"] for row in rows] == [2]
    assert rows[0]["hit"] == "Algorithm  Design"
    assert rows[0]["pre"] == ""
    assert rows[0]["occurrences"] == [
        {"char_start": 100, "char_end": 117},
        {"char_start": 123, "char_end": 139},
    ]
    assert rows[0]["occurrence_count"] == 2
    assert rows.candidate_scanned == 2
    assert rows.candidate_limit_reached is False


@pytest.mark.asyncio
async def test_phrase_mode_reports_a_candidate_cap_even_when_verification_emits_zero(
    monkeypatch,
):
    from doc_ray.adapters import persistence_sentence_search as phrase_read

    monkeypatch.setattr(phrase_read, "PHRASE_CANDIDATE_SCAN_CAP", 3)
    conn = _Conn(
        [
            [
                _candidate(f"01ARZ3NDEKTSV4RRFFQ69G5F{i:02d}", "of. it", start=i * 10, ordinal=i)
                for i in range(3)
            ]
        ]
    )

    rows = await read.search_sentences(conn, "of it", match="phrase", limit=2)

    assert rows == []
    assert rows.candidate_scanned == 3
    assert rows.candidate_limit == 3
    assert rows.candidate_limit_reached is True
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_phrase_mode_overfetches_keyset_batches_until_a_verified_page_can_fill(
    monkeypatch,
):
    from doc_ray.adapters import persistence_sentence_search as phrase_read

    monkeypatch.setattr(phrase_read, "PHRASE_CANDIDATE_BATCH_SIZE", 2)
    conn = _Conn(
        [
            [
                _candidate("01ARZ3NDEKTSV4RRFFQ69G5F10", "algorithm designed", start=0, ordinal=0),
                _candidate("01ARZ3NDEKTSV4RRFFQ69G5F11", "design algorithm", start=30, ordinal=1),
            ],
            [
                _candidate("01ARZ3NDEKTSV4RRFFQ69G5F12", "Algorithm Design", start=60, ordinal=2),
            ],
        ]
    )

    rows = await read.search_sentences(conn, "algorithm design", match="phrase", limit=2)

    assert [row["ordinal"] for row in rows] == [2]
    assert rows.candidate_scanned == 3
    assert len(conn.calls) == 2
    assert conn.calls[1][1]["after_ord"] == 1


@pytest.mark.asyncio
async def test_candidate_scan_count_excludes_unverified_batch_tail_after_page_fills(
    monkeypatch,
):
    from doc_ray.adapters import persistence_sentence_search as phrase_read

    monkeypatch.setattr(phrase_read, "PHRASE_CANDIDATE_BATCH_SIZE", 2)
    conn = _Conn(
        [
            [
                _candidate("01ARZ3NDEKTSV4RRFFQ69G5F20", "Algorithm Design", start=0, ordinal=0),
                _candidate("01ARZ3NDEKTSV4RRFFQ69G5F21", "algorithm designed", start=30, ordinal=1),
            ]
        ]
    )

    rows = await read.search_sentences(conn, "algorithm design", match="phrase", limit=1)

    assert [row["ordinal"] for row in rows] == [0]
    assert rows.candidate_scanned == 1


@pytest.mark.asyncio
async def test_default_substring_call_keeps_the_historical_query_and_row_shape():
    row = _candidate("01ARZ3NDEKTSV4RRFFQ69G5FAC", "Tangram and ngram", start=0, ordinal=0)
    conn = _Conn([[row]])

    rows = await read.search_sentences(conn, "ngram")

    assert rows[0]["hit"] == "ngram"  # the first substring is inside Tangram
    assert "occurrences" not in rows[0]
    sql, _params = conn.calls[0]
    assert "JOIN spans" not in sql
