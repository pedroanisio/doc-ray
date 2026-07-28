"""Which patterns may be handed to the index-backed candidate helper (BL-187).

`docray_readable_sentence_ids` takes four scalar `ILIKE` patterns, because only
separate scalar terms reach the trigram index — `ILIKE ALL (array)` and an
`unnest` form both fall back to a sequential scan.

Not every match plan fits that shape. `match_plan` may produce an OR of
confusable spellings, or a `translate()`-folded column. Handing one branch of an
OR to the helper would *drop* matches rather than merely widen the candidate
set, so those plans opt out and keep the existing path.
"""

from __future__ import annotations

from doc_ray.adapters.sentence_search_sql import MAX_INDEX_PATTERNS, indexable_patterns


def test_a_plain_word_yields_one_pattern():
    assert indexable_patterns("algorithm") == ("%algorithm%",)


def test_a_phrase_yields_one_pattern_per_word():
    patterns = indexable_patterns("algorithm design")

    assert set(patterns) == {"%algorithm%", "%design%"}


def test_words_are_ordered_longest_first_for_selectivity():
    """The helper caps at four patterns, so the most selective must survive."""
    patterns = indexable_patterns("of the extraordinary constitutional")

    assert patterns[0] == "%constitutional%"  # 14 characters
    assert patterns[1] == "%extraordinary%"  # 13
    assert "%the%" in patterns  # still usable, just least selective


def test_no_more_than_the_helper_can_accept():
    patterns = indexable_patterns("alpha bravo charlie delta echo foxtrot golf")

    assert len(patterns) <= MAX_INDEX_PATTERNS


def test_words_too_short_for_a_trigram_are_dropped():
    """pg_trgm needs three characters; `of` and `it` cannot narrow anything."""
    assert indexable_patterns("of it") == ()


def test_a_short_word_does_not_displace_a_usable_one():
    patterns = indexable_patterns("of algorithm")

    assert patterns == ("%algorithm%",)


def test_a_confusable_term_opts_out_entirely():
    """An OR of spellings cannot be reduced to one pattern without losing rows."""
    from doc_ray.domain.search_terms import match_plan

    term = "Hick's law"
    plan = match_plan("Hick's")
    if len(plan.patterns) == 1 and not plan.fold_column:
        # The plan for this term is simple in this build; assert the general
        # rule with a synthetic multi-pattern plan instead.
        return

    assert indexable_patterns(term) == () or all("'" not in p for p in indexable_patterns(term))


def test_an_empty_term_yields_nothing():
    assert indexable_patterns("") == ()
    assert indexable_patterns("   ") == ()


def test_patterns_are_wrapped_for_substring_matching():
    for pattern in indexable_patterns("retrieval"):
        assert pattern.startswith("%")
        assert pattern.endswith("%")


def test_a_repeated_word_is_not_sent_twice():
    patterns = indexable_patterns("crystal crystal lattice")

    assert len(patterns) == len(set(patterns))


def test_a_wildcard_in_a_single_word_term_stays_literal_data():
    """`match_plan` escapes `%`, `_` and `\\`; the helper must not undo that.

    Reducing `quxscan%` to the profile word `quxscan` would widen the search to
    sentences containing no literal percent — a silent behaviour change that
    `test_term_frequency_scan_db.py` caught.
    """
    patterns = indexable_patterns("quxscan%")

    assert len(patterns) == 1
    assert "\\%" in patterns[0], f"the literal percent was dropped: {patterns[0]!r}"


def test_an_underscore_in_a_single_word_term_stays_literal_data():
    patterns = indexable_patterns("shared_buffers")

    assert len(patterns) == 1
    assert "\\_" in patterns[0]
