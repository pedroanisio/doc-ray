"""Tests for the ULID implementation."""

from __future__ import annotations

import re

import pytest

from doc_ray.domain.ulid import (
    ULID,
    ULID_REGEX,
    ULIDFactory,
    _MAX_RAND,
    new_ulid_str,
)

_RE = re.compile(ULID_REGEX)


# --- format -----------------------------------------------------------------

def test_string_is_26_crockford_chars():
    s = new_ulid_str()
    assert len(s) == 26
    assert _RE.match(s), s
    assert set(s) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_leading_char_is_0_to_7_for_realistic_timestamps():
    # 48-bit ms timestamps up to year ~10889 keep the first char in 0..7.
    for ts in (0, 1_700_000_000_000, _MAX_RAND & ((1 << 48) - 1)):
        s = str(ULID(ts % (1 << 48), 0))
        assert s[0] in "01234567", s


def test_format_matches_db_domain_regex():
    # The same regex the Postgres `ulid` domain enforces.
    for _ in range(200):
        assert _RE.match(new_ulid_str())


# --- round-trip -------------------------------------------------------------

@pytest.mark.parametrize("ts,rand", [
    (0, 0),
    (1, 1),
    (1_700_000_000_000, 123456789),
    ((1 << 48) - 1, _MAX_RAND),
])
def test_encode_decode_roundtrip(ts, rand):
    u = ULID(ts, rand)
    back = ULID.parse(str(u))
    assert back == u
    assert back.timestamp_ms == ts
    assert back.randomness == rand


def test_timestamp_decodes_to_expected_datetime():
    u = ULID(1_700_000_000_000, 0)
    assert u.datetime.year == 2023
    assert int(u.datetime.timestamp() * 1000) == 1_700_000_000_000


def test_parse_rejects_wrong_length():
    with pytest.raises(ValueError):
        ULID.parse("TOOSHORT")


def test_constructor_rejects_out_of_range():
    with pytest.raises(ValueError):
        ULID(1 << 48, 0)
    with pytest.raises(ValueError):
        ULID(0, _MAX_RAND + 1)


# --- monotonicity & sortability --------------------------------------------

def test_lexical_order_equals_chronological_order():
    early = str(ULID(1_000, 999))          # later randomness, earlier time
    late = str(ULID(2_000, 0))             # earlier randomness, later time
    assert early < late                    # timestamp dominates the ordering


def test_monotonic_within_same_millisecond():
    # Freeze the clock: every new() lands in the same ms -> randomness must
    # strictly increase, so the strings are strictly increasing too.
    f = ULIDFactory(clock=lambda: 5_000, rng=lambda: 42)
    seq = [str(f.new()) for _ in range(1000)]
    assert seq == sorted(seq)
    assert len(set(seq)) == 1000
    # each shares the same 10-char timestamp prefix
    assert len({s[:10] for s in seq}) == 1


def test_randomness_overflow_rolls_into_next_millisecond():
    # Start randomness at the max; the next same-ms id must bump the timestamp.
    f = ULIDFactory(clock=lambda: 7_000, rng=lambda: _MAX_RAND)
    a = f.new()
    b = f.new()
    assert a.timestamp_ms == 7_000 and a.randomness == _MAX_RAND
    assert b.timestamp_ms == 7_001          # rolled over
    assert str(b) > str(a)


def test_monotonic_across_advancing_clock():
    ticks = iter([10, 10, 11, 12, 12, 12, 13])
    f = ULIDFactory(clock=lambda: next(ticks), rng=lambda: 0)
    seq = [str(f.new()) for _ in range(7)]
    assert seq == sorted(seq)
    assert len(set(seq)) == 7
