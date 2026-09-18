"""A value bound for a unique btree must fit in one, whatever the file says.

The failure this pins: an ingest died at "Persisting extracted
document" with

    index row size 3184 exceeds btree version 4 maximum 2704
    for index "authors_name_key"

`authors.name` is `text NOT NULL UNIQUE` (migration 004), so PostgreSQL builds a
btree over it, and a btree entry cannot exceed roughly a third of an 8 KB page.
Nothing between a PDF's `/Author` metadata field and the INSERT bounded the
value, so a file that dumps a whole author list — or an abstract — into that
field kills the ingest *after* extraction, sentencing and the object-store
upload have all been paid for.

Truncation alone would be the wrong fix: two different long names would collapse
into one author. The bound therefore keeps a digest of the full value, so
distinct inputs stay distinct.
"""

from __future__ import annotations

import pytest

from doc_ray.domain import indexed_text as it


def test_a_short_value_is_returned_untouched():
    """The overwhelmingly common case must not be rewritten at all."""
    assert it.bound_for_index("Ada Lovelace") == "Ada Lovelace"


def test_a_value_at_the_limit_is_still_untouched():
    value = "a" * it.MAX_BTREE_INDEX_BYTES

    assert it.bound_for_index(value) == value


def test_the_bound_leaves_room_for_the_index_row_header():
    """A value of exactly the server's limit still overflows.

    Measured: a 2704-byte name produced `index row size 2720`, because the limit
    covers the whole index row — value plus tuple header and alignment — not the
    value alone. The bound must therefore sit strictly below it.
    """
    assert it.MAX_BTREE_INDEX_BYTES < it.BTREE_MAX_INDEX_ROW_BYTES

    bounded = it.bound_for_index("k" * 5000)

    assert len(bounded.encode("utf-8")) + it.INDEX_ROW_OVERHEAD_BYTES <= (
        it.BTREE_MAX_INDEX_ROW_BYTES
    )


def test_the_reported_failure_now_fits_the_index():
    """3184 bytes is the exact size from the production error."""
    bounded = it.bound_for_index("A" * 3184)

    assert len(bounded.encode("utf-8")) <= it.MAX_BTREE_INDEX_BYTES


@pytest.mark.parametrize("size", [2705, 3184, 8192, 100_000])
def test_any_oversized_value_fits_after_bounding(size: int):
    bounded = it.bound_for_index("x" * size)

    assert len(bounded.encode("utf-8")) <= it.MAX_BTREE_INDEX_BYTES


def test_two_distinct_long_values_stay_distinct():
    """Plain truncation would merge these into one author row."""
    shared = "Z" * 5000

    assert it.bound_for_index(shared + "first") != it.bound_for_index(shared + "second")


def test_bounding_is_deterministic():
    """The same author in two documents must resolve to the same row."""
    value = "Q" * 4000

    assert it.bound_for_index(value) == it.bound_for_index(value)


def test_the_readable_head_of_the_value_survives():
    """A bounded name is still recognisable to a human reading the row."""
    bounded = it.bound_for_index("Grace Hopper " + "y" * 5000)

    assert bounded.startswith("Grace Hopper ")


def test_a_bounded_value_is_marked_as_truncated():
    assert it.TRUNCATION_MARK in it.bound_for_index("m" * 4000)
    assert it.TRUNCATION_MARK not in it.bound_for_index("Ada Lovelace")


# --- byte counting, not character counting -----------------------------------


def test_the_limit_counts_bytes_because_the_btree_does():
    """A multibyte name is far shorter in characters than in bytes.

    Bounding on `len(str)` would let a 3-byte-per-character name through at
    three times the byte budget and reproduce the original failure.
    """
    value = "☃" * 2000  # 3 bytes each in UTF-8 -> 6000 bytes

    bounded = it.bound_for_index(value)

    assert len(bounded.encode("utf-8")) <= it.MAX_BTREE_INDEX_BYTES


def test_bounding_never_splits_a_character():
    """A truncation mid-codepoint would produce an undecodable value."""
    bounded = it.bound_for_index("é" * 4000)

    bounded.encode("utf-8").decode("utf-8")  # must not raise


def test_bounding_never_splits_a_surrogate_pair():
    bounded = it.bound_for_index("😀" * 2000)

    bounded.encode("utf-8").decode("utf-8")


# --- edges -------------------------------------------------------------------


def test_an_empty_value_is_preserved():
    assert it.bound_for_index("") == ""


def test_a_custom_limit_is_honoured():
    bounded = it.bound_for_index("n" * 500, limit=100)

    assert len(bounded.encode("utf-8")) <= 100


def test_a_limit_too_small_to_hold_the_digest_is_rejected():
    """Below the digest width the function cannot keep values distinct."""
    with pytest.raises(ValueError):
        it.bound_for_index("n" * 500, limit=4)
