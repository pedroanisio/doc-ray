"""Unit tests for headings._repair_tree — pure monotonic-nesting repair.

_repair_tree enforces that no heading jumps more than +1 deeper than the
running previous (repaired) level. It re-bases so the first heading can never
be deeper than 1. It preserves list length and all non-level fields.
"""

import pytest

import headings


def _mk(levels):
    """Build a fresh list of Heading objects with distinct text/ordinal."""
    return [
        headings.Heading(
            level=lvl,
            text=f"h{i}",
            page=0,
            ordinal=i,
            confidence=0.9,
            signals={},
        )
        for i, lvl in enumerate(levels)
    ]


def _out_levels(input_levels):
    return [h.level for h in headings._repair_tree(_mk(input_levels))]


# --- the eight canonical cases -------------------------------------------
@pytest.mark.parametrize(
    "given, expected",
    [
        ([1, 3], [1, 2]),
        ([1, 2, 4, 2], [1, 2, 3, 2]),
        ([2], [1]),
        ([1, 4], [1, 2]),
        ([1, 2, 2, 1], [1, 2, 2, 1]),        # already valid -> unchanged
        ([1, 2, 3, 4], [1, 2, 3, 4]),        # already valid -> unchanged
        ([], []),                            # empty -> empty
        ([3, 1, 2], [1, 1, 2]),              # first re-based down to 1
    ],
)
def test_repair_levels(given, expected):
    assert _out_levels(given) == expected


# --- structural invariants over many inputs ------------------------------
_CASES = [
    [],
    [1],
    [2],
    [5],
    [1, 3],
    [1, 4],
    [1, 2, 2, 1],
    [1, 2, 3, 4],
    [1, 2, 4, 2],
    [3, 1, 2],
    [4, 4, 4],
    [1, 3, 3, 5, 2],
    [2, 4, 1, 4, 4, 1],
]


@pytest.mark.parametrize("given", _CASES)
def test_never_increases_by_more_than_one(given):
    """No output level exceeds the previous output level by more than +1,
    and the very first output level is at most 1."""
    out = [h.level for h in headings._repair_tree(_mk(given))]
    prev = 0
    for lvl in out:
        assert lvl <= prev + 1, f"jump >+1 at {lvl} after prev {prev} in {given}"
        prev = lvl


@pytest.mark.parametrize("given", _CASES)
def test_first_level_at_most_one(given):
    out = [h.level for h in headings._repair_tree(_mk(given))]
    if out:
        assert out[0] <= 1


@pytest.mark.parametrize("given", _CASES)
def test_preserves_length(given):
    assert len(headings._repair_tree(_mk(given))) == len(given)


@pytest.mark.parametrize("given", _CASES)
def test_preserves_text_and_ordinal(given):
    inp = _mk(given)
    out = headings._repair_tree(inp)
    assert [h.text for h in out] == [f"h{i}" for i in range(len(given))]
    assert [h.ordinal for h in out] == list(range(len(given)))
    # confidence and page untouched too
    assert all(h.confidence == 0.9 for h in out)
    assert all(h.page == 0 for h in out)


def test_repair_never_lowers_below_input_when_already_shallow():
    """Repair only clamps DEEP jumps; a level that is <= prev+1 is left as-is
    (it does not force strict +1 increments)."""
    # 1 -> 1 (stays), then 1 (1 <= 1+1) stays 1, then 1 stays 1.
    assert _out_levels([1, 1, 1]) == [1, 1, 1]
    # A drop is always allowed verbatim.
    assert _out_levels([1, 2, 3, 1]) == [1, 2, 3, 1]


def test_deep_run_is_clamped_to_incremental_ladder():
    """A monotonically deepening run that skips levels becomes a +1 ladder,
    but only up to what the running prev allows."""
    # 1, then 5 -> clamp to 2, then 5 -> prev is 2 so clamp to 3, etc.
    assert _out_levels([1, 5, 5, 5, 5]) == [1, 2, 3, 4, 5]


def test_returns_same_objects_mutated_in_place():
    """_repair_tree mutates and returns the same Heading instances (documents
    current behavior: it is not a pure copy)."""
    inp = _mk([1, 4])
    out = headings._repair_tree(inp)
    assert out is not inp or True  # list may be new
    # the deep node was mutated in place
    assert inp[1].level == 2
    assert out[1] is inp[1]
