"""The literal-substring matching rule: escaped, folded, and honestly windowed.

Three defects, one root cause — "literal substring" was a promise in the tool
descriptions and the coverage notes, but every surface implemented its own
approximation of it:

1. ``%`` and ``_`` in a term were forwarded to Postgres as wildcards.
2. A term typed with ``'`` never matched a corpus that stores ``’``, and the
   zero was reported as an *exhaustive* absence.
3. The KWIC window was split case-sensitively while the match was
   case-insensitive, so a real hit could come back with an empty ``hit``.

These are pure-logic tests (no database): the plan is decided here, and
``sentence_search_sql`` only renders it.
"""

from __future__ import annotations

import pytest

from doc_ray.adapters import sentence_search_sql
from doc_ray.domain import search_terms as st


def _spellings(term: str) -> set[str]:
    """The exact spellings a plan matches, unwrapped from its ``%…%`` patterns."""
    return {p[1:-1] for p in st.match_plan(term).patterns}


# --------------------------------------------------------------------------
# literal means literal
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "escaped"),
    [
        ("shared_buffers", "shared\\_buffers"),
        ("100%", "100\\%"),
        ("back\\slash", "back\\\\slash"),
    ],
)
def test_like_metacharacters_are_escaped_so_a_literal_term_stays_literal(term, escaped):
    # BL-164 sends underscore compounds here precisely because term_frequency
    # cannot count them; forwarding `_` as a wildcard would make this the wrong
    # destination for them.
    assert _spellings(term) == {escaped}


def test_a_plain_term_expands_to_exactly_one_pattern():
    # The common path must stay shape-identical to the single ILIKE it replaced,
    # so the trigram index is used exactly as before.
    plan = st.match_plan("sandworm")
    assert plan.patterns == ("%sandworm%",)
    assert plan.fold_column is False


# --------------------------------------------------------------------------
# typographic folding
# --------------------------------------------------------------------------


def test_a_typed_ascii_apostrophe_matches_the_typographic_one():
    # The reported failure: `Hick's` returned 0 hits against a corpus that
    # stores `Hick’s`, and the note called that scan exhaustive.
    assert "Hick’s" in _spellings("Hick's")


def test_a_typed_typographic_apostrophe_matches_the_ascii_one():
    # Symmetric: copy-pasting a quote out of the document must still find text
    # that was extracted with a plain apostrophe.
    assert "Hick's" in _spellings("Hick’s")


def test_dashes_and_double_quotes_fold_across_their_typographic_forms():
    assert "well–being" in _spellings("well-being")
    assert "“scale”" in _spellings('"scale"')


def test_folding_never_narrows_the_match():
    # Widening only: whatever the caller typed is always among the spellings.
    for term in ("Hick's", "well-being", '"scale"', "plain"):
        assert term in _spellings(term)


def test_the_fold_is_length_preserving_so_offsets_survive_it():
    # Citation handles (charStart/charEnd) are only exact if folding cannot
    # shift an offset.
    for text in ("Hick’s law — “scale”", "plain ascii", "‘quoted‘"):
        assert len(st.fold(text)) == len(text)


def test_a_term_too_confusable_to_enumerate_folds_the_column_instead():
    # Three or more confusables would exceed MAX_PATTERNS; correctness is kept
    # by folding the column, at the cost of the index.
    plan = st.match_plan("it’s—“x”")
    assert plan.fold_column is True
    assert plan.patterns == ('%it\'s-"x"%',)


def test_the_pattern_count_is_bounded():
    assert len(st.match_plan("it's-\"a\"-'b'").patterns) <= st.MAX_PATTERNS


def test_the_fold_table_and_its_sql_translate_form_agree():
    # The fallback plan folds the column with these two strings; a mismatch in
    # length or order would silently fold to the wrong characters.
    assert len(st.FOLD_SOURCE) == len(st.FOLD_TARGET)
    assert st.fold(st.FOLD_SOURCE) == st.FOLD_TARGET


# --------------------------------------------------------------------------
# KWIC windows
# --------------------------------------------------------------------------


def test_kwic_splits_case_insensitively_because_the_match_was_case_insensitive():
    # ILIKE returns this row; a case-sensitive partition would hand back an
    # empty `hit` and the whole sentence as `pre`.
    assert st.kwic("Hick's law is a thing", "hick's") == ("", "Hick's", " law is a thing")


def test_kwic_reports_the_documents_spelling_not_the_callers():
    # Quoting the caller's `'` back as if the source used it would misquote the
    # document; every window is a citation.
    pre, hit, post = st.kwic("The value of Hick’s law", "Hick's")
    assert (pre, hit, post) == ("The value of ", "Hick’s", " law")


def test_kwic_returns_the_whole_text_as_pre_when_the_term_is_absent():
    assert st.kwic("nothing here", "spice") == ("nothing here", "", "")


def test_kwic_is_offset_exact_for_a_folded_hit():
    text = "a “quoted” span"
    pre, hit, post = st.kwic(text, '"quoted"')
    assert pre + hit + post == text


# --------------------------------------------------------------------------
# phrase-mode candidate SQL (#45)
# --------------------------------------------------------------------------


def test_phrase_candidates_are_an_and_of_parameterized_words_with_a_selective_lead():
    """Every true phrase is a candidate; longer indexable words lead the AND."""
    builder = sentence_search_sql.phrase_candidate_clause

    sql, params = builder("s.text", "of Algorithm Design")

    assert sql.count("ILIKE") == 3
    assert " AND " in sql
    assert "algorithm" in next(iter(params.values()))
    assert all(word not in sql.lower() for word in ("algorithm", "design", "of"))


def test_phrase_candidate_sql_keeps_like_metacharacters_as_data():
    builder = sentence_search_sql.phrase_candidate_clause

    sql, params = builder("s.text", "shared_buffers algorithm")

    assert "%shared\\_buffers%" in params.values()
    assert "shared_buffers" not in sql
