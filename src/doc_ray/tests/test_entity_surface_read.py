"""Bounded read-model contracts for exact-surface NER correction previews."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from doc_ray.adapters.persistence_entity_surface_read import entity_surface_occurrences

_DOC_A = "11111111-1111-1111-1111-111111111111"
_DOC_B = "22222222-2222-2222-2222-222222222222"


class _Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, sql: str, params: dict[str, object]) -> _Cursor:
        self.calls.append((" ".join(sql.split()), params))
        return _Cursor(self.rows)


def test_preview_matches_exact_surface_and_type_with_per_document_bound() -> None:
    conn = _Connection([])

    result = asyncio.run(
        entity_surface_occurrences(
            conn,
            [_DOC_A, _DOC_B],
            surface="Apple",
            original_type="ORG",
            sample_limit=3,
            count_cap=25,
        )
    )

    sql, params = conn.calls[0]
    assert "em.surface = %(surface)s" in sql
    assert "em.entity_type = %(original_type)s" in sql
    assert "unnest(%(document_ids)s::uuid[])" in sql
    assert "LEFT JOIN LATERAL" in sql
    assert "LIMIT %(scan_limit)s" in sql
    assert "ILIKE" not in sql and "regexp" not in sql.lower()
    assert "count(" not in sql.lower() and "group by" not in sql.lower()
    assert params == {
        "document_ids": [_DOC_A, _DOC_B],
        "surface": "Apple",
        "original_type": "ORG",
        "scan_limit": 26,
    }
    assert result == [
        {
            "document_id": _DOC_A,
            "surface": "Apple",
            "original_type": "ORG",
            "occurrence_count": 0,
            "count_cap": 25,
            "count_capped": False,
            "samples": [],
        },
        {
            "document_id": _DOC_B,
            "surface": "Apple",
            "original_type": "ORG",
            "occurrence_count": 0,
            "count_cap": 25,
            "count_capped": False,
            "samples": [],
        },
    ]


def test_preview_exposes_capped_count_and_sentence_context_samples() -> None:
    rows = [
        {
            "document_id": _DOC_A,
            "mention_id": "01ARZ3NDEKTSV4RRFFQ69G5FA1",
            "char_start": 4,
            "char_end": 9,
            "sentence_ordinal": 1,
            "sentence_text": "The Apple board met.",
        },
        {
            "document_id": _DOC_A,
            "mention_id": "01ARZ3NDEKTSV4RRFFQ69G5FA2",
            "char_start": 40,
            "char_end": 45,
            "sentence_ordinal": 3,
            "sentence_text": "Apple shipped it.",
        },
        {
            "document_id": _DOC_A,
            "mention_id": "01ARZ3NDEKTSV4RRFFQ69G5FA3",
            "char_start": 80,
            "char_end": 85,
            "sentence_ordinal": 5,
            "sentence_text": "A third Apple mention.",
        },
        {
            "document_id": _DOC_B,
            "mention_id": None,
            "char_start": None,
            "char_end": None,
            "sentence_ordinal": None,
            "sentence_text": None,
        },
    ]
    conn = _Connection(rows)

    result = asyncio.run(
        entity_surface_occurrences(
            conn,
            [_DOC_A, _DOC_B],
            surface="Apple",
            original_type="ORG",
            sample_limit=1,
            count_cap=2,
        )
    )

    assert result[0]["occurrence_count"] == 2
    assert result[0]["count_cap"] == 2
    assert result[0]["count_capped"] is True
    assert result[0]["samples"] == [
        {
            "mention_id": "01ARZ3NDEKTSV4RRFFQ69G5FA1",
            "char_start": 4,
            "char_end": 9,
            "sentence_ordinal": 1,
            "sentence_context": "The Apple board met.",
        }
    ]
    assert result[1]["occurrence_count"] == 0
    assert result[1]["count_capped"] is False
    assert result[1]["samples"] == []


@pytest.mark.parametrize(
    ("document_ids", "surface", "original_type", "sample_limit", "count_cap", "message"),
    [
        ([], "Apple", "ORG", 3, 25, "document_ids"),
        ([_DOC_A], "", "ORG", 3, 25, "surface"),
        ([_DOC_A], "Apple", "", 3, 25, "original_type"),
        ([_DOC_A] * 201, "Apple", "ORG", 3, 25, "at most 200"),
        ([_DOC_A], "Apple", "ORG", 0, 25, "sample_limit"),
        ([_DOC_A], "Apple", "ORG", 3, 0, "count_cap"),
    ],
)
def test_preview_rejects_unbounded_or_ambiguous_inputs(
    document_ids: list[str],
    surface: str,
    original_type: str,
    sample_limit: int,
    count_cap: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        asyncio.run(
            entity_surface_occurrences(
                _Connection([]),
                document_ids,
                surface=surface,
                original_type=original_type,
                sample_limit=sample_limit,
                count_cap=count_cap,
            )
        )


def test_d024_guard_scans_the_preview_adapter_without_findings() -> None:
    import scripts.check_read_path_aggregates as guard

    path = "src/doc_ray/adapters/persistence_entity_surface_read.py"
    assert path in guard._READ_PATHS
    assert guard.find_violations((path,)) == []
    source = Path(path).read_text()
    assert "count(" not in source.lower()
    assert "group by" not in source.lower()
