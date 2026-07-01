"""Shared structural invariants for extracted outlines.

Import from test modules; do not modify. `assert_valid_outline` encodes the
guarantees `extract_headings` must always uphold regardless of input.
"""

from __future__ import annotations


def assert_valid_outline(headings, page_count=None, require_monotonic=True):
    """Assert the universal invariants of an extracted heading list.

    - levels are ints in 1..4
    - text is non-empty after stripping
    - confidence is in [0.0, 1.0]
    - ordinals are strictly increasing
    - pages are within [0, page_count) when page_count is given
    - (heuristic path) nesting is monotonic: a level never jumps more than
      +1 deeper than the previous heading. TOC fast-path trusts the author's
      outline verbatim, so pass require_monotonic=False for it.
    """
    prev_ordinal = -1
    prev_level = 0
    for h in headings:
        assert isinstance(h.level, int), f"level not int: {h.level!r}"
        assert 1 <= h.level <= 4, f"level out of range: {h.level}"
        assert h.text and h.text.strip(), f"empty heading text: {h.text!r}"
        assert 0.0 <= h.confidence <= 1.0, f"confidence out of range: {h.confidence}"
        assert h.ordinal > prev_ordinal, f"ordinal not increasing: {h.ordinal}"
        prev_ordinal = h.ordinal
        if page_count is not None:
            assert 0 <= h.page < page_count, f"page out of range: {h.page}"
        if require_monotonic:
            assert h.level <= prev_level + 1, (
                f"level jump {prev_level}->{h.level} at ordinal {h.ordinal}"
            )
        prev_level = h.level


def texts(headings):
    return [h.text for h in headings]


def levels(headings):
    return [h.level for h in headings]
