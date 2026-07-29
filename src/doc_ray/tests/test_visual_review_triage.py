"""BL-197: the review queue must be orderable and filterable by grounding.

The lane returned proposals newest-first with no ordering by score and no band
filter, so the first screen an operator saw was an unrepresentative sample of
the worst items. The conclusion drawn — that the whole corpus was garbage — was
wrong: 93.7% of captions are well-formed prose. Ordering determines what an
operator believes about the corpus, so it is a correctness concern, not a
cosmetic one.

These tests pin the read contract; the SQL is asserted directly because the
queue's ordering is the behaviour under test.
"""

from __future__ import annotations

import inspect

import pytest

from doc_ray.adapters import persistence_visual_read as reader


def _signature() -> inspect.Signature:
    return inspect.signature(reader.list_visual_review)


def test_the_lane_accepts_an_explicit_sort() -> None:
    assert "sort" in _signature().parameters, (
        "an operator must be able to reach the items most likely to be approved"
    )


def test_the_lane_accepts_a_grounding_band_filter() -> None:
    params = _signature().parameters
    assert "min_grounding" in params and "max_grounding" in params


def test_an_unknown_sort_is_rejected_rather_than_silently_ignored() -> None:
    """A typo must not quietly return the default order."""
    with pytest.raises(ValueError, match="sort"):
        reader._sort_clause("groundingg_desc")


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("grounding_desc", "vr.grounding_score DESC"),
        ("grounding_asc", "vr.grounding_score ASC"),
        ("newest", "vr.created_at DESC"),
        ("oldest", "vr.created_at ASC"),
    ],
)
def test_each_supported_sort_maps_to_a_deterministic_order(sort: str, expected: str) -> None:
    clause = reader._sort_clause(sort)
    assert clause.startswith(expected)
    # A stable tiebreaker is required or keyset pagination can skip or repeat rows.
    assert "vr.id" in clause


def test_the_default_sort_surfaces_the_most_approvable_items_first() -> None:
    """Highest grounding first: the opposite of what the operator met."""
    assert reader.DEFAULT_REVIEW_SORT == "grounding_desc"


def test_sorts_are_an_allowlist_not_interpolated_input() -> None:
    """The sort reaches an ORDER BY, so it must never be caller-controlled text."""
    for candidate in ("grounding_score; DROP TABLE visual_caption", "1", ""):
        with pytest.raises(ValueError):
            reader._sort_clause(candidate)


def test_the_band_filter_is_applied_in_sql_not_after_the_limit() -> None:
    """Filtering after LIMIT would return short pages and hide matching rows."""
    source = inspect.getsource(reader.list_visual_review)
    where = source[source.index("WHERE") : source.index("ORDER BY")]
    assert "grounding_score" in where, "the band filter must be part of the WHERE clause"
