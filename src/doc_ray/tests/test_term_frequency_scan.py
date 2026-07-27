"""The sentence-scan verdict: measure phrases instead of declining them (#39).

The lexeme index structurally cannot hold a multi-word phrase, so the pre-flight
answered such terms with evidence *about* the term (component counts) rather than
a measurement *of* it. These tests pin the measured path: a bounded count of
sentences matching the same literal-substring rule ``search_sentences`` uses, so
the pre-flight predicts the tool that will actually serve the query.

Pure domain — no database. The adapter half lives in
``test_term_frequency_scan_db.py``.
"""

from __future__ import annotations

import pytest

from doc_ray.domain import term_frequency as tf

_TOTAL_SENTENCES = 1_500_000
_TOTAL_USES = 32_000_000


def _prepared(term: str) -> tf.PreparedTerm:
    return tf.prepare_terms([term])[0]


def _verdict(term: str, scan: tf.SentenceScan | None, counts: dict[str, int] | None = None):
    prepared = tf.prepare_terms([term])
    scans = {} if scan is None else {prepared[0].key: scan}
    return tf.build_verdicts(
        prepared,
        counts or {},
        _TOTAL_USES,
        scans=scans,
        total_sentences=_TOTAL_SENTENCES,
    )[0]


# --- TEST-D4: the mirrored page-size constant -------------------------------


def test_sentence_page_size_mirrors_the_platform_page_cap() -> None:
    """The low/moderate boundary is "fits in one page", so it must BE one page.

    The domain must not import an entrypoint (IC-1), so the constant is mirrored
    and kept honest here — the one place the import is allowed.
    """
    from doc_ray.entrypoints.api.context import MAX_PAGE_SIZE

    assert tf.SENTENCE_PAGE_SIZE == MAX_PAGE_SIZE


def test_high_threshold_is_the_count_cap() -> None:
    """Counting past the high threshold buys nothing: the band is already decided."""
    assert tf.SENTENCE_HIGH_HITS > tf.SENTENCE_PAGE_SIZE


# --- TEST-D1: bands ---------------------------------------------------------


@pytest.mark.parametrize(
    ("hits", "capped", "expected"),
    [
        (0, False, tf.LOW),
        (1, False, tf.LOW),
        (199, False, tf.LOW),
        (200, False, tf.MODERATE),
        (2000, False, tf.MODERATE),
        (2001, False, tf.HIGH),
        (5000, False, tf.HIGH),
        # A capped count is a floor: the true total is unknown and at least the
        # cap, which is by definition already in the high band.
        (2001, True, tf.HIGH),
    ],
)
def test_sentence_flood_risk_bands(hits: int, capped: bool, expected: str) -> None:
    assert tf.sentence_flood_risk(hits, capped) == expected


def test_a_scanned_phrase_is_measured_not_declined() -> None:
    """The defect this closes: a phrase came back with no verdict of its own."""
    verdict = _verdict("Model Context Protocol", tf.SentenceScan(hits=412, capped=False))

    assert verdict.method == tf.METHOD_SENTENCE_SCAN
    assert verdict.flood_risk == tf.MODERATE
    assert verdict.sentence_hits == 412
    assert verdict.total_sentences == _TOTAL_SENTENCES
    assert verdict.capped is False
    # Adjacency is now PROVEN by the literal-substring match — the exact claim
    # the component upper bound could not make.
    assert verdict.attested is True


def test_a_scanned_phrase_keeps_its_component_evidence() -> None:
    """The lexeme-side numbers stay as secondary evidence, not replaced."""
    verdict = _verdict(
        "Model Context Protocol",
        tf.SentenceScan(hits=412, capped=False),
        counts={"model": 900, "context": 700, "protocol": 500},
    )

    assert [part.part for part in verdict.parts] == ["model", "context", "protocol"]
    assert verdict.measurement == tf.COMPONENT_UPPER_BOUND
    assert verdict.uses == 500  # rarest component, unchanged


def test_a_capped_scan_reports_a_floor_never_a_total() -> None:
    """IC-2: a bound that is invisible is the BL-162 defect class."""
    verdict = _verdict("of the", tf.SentenceScan(hits=tf.SENTENCE_HIGH_HITS, capped=True))

    assert verdict.capped is True
    assert verdict.flood_risk == tf.HIGH
    assert verdict.note is not None
    assert "at least" in verdict.note


def test_a_scanned_compound_is_measured_too() -> None:
    """OD-2: underscore compounds and alphanumerics scan identically to phrases.

    ``shared_buffers`` is the BL-164 term that started all of this. It has no
    lexeme count and never will, but it is a perfectly good literal substring.
    """
    verdict = _verdict("shared_buffers", tf.SentenceScan(hits=37, capped=False))

    assert verdict.method == tf.METHOD_SENTENCE_SCAN
    assert verdict.flood_risk == tf.LOW
    assert verdict.sentence_hits == 37
    assert verdict.attested is True
    # No lexeme count exists for a compound, so none is invented (IC-5/BL-164).
    assert verdict.uses is None
    assert verdict.share is None


def test_a_scanned_term_with_no_hits_states_literal_absence() -> None:
    """Zero is a measurement here, unlike the BL-164 impossible lookup."""
    verdict = _verdict("Kwisatz Haderach Protocol", tf.SentenceScan(hits=0, capped=False))

    assert verdict.method == tf.METHOD_SENTENCE_SCAN
    assert verdict.flood_risk == tf.LOW
    assert verdict.sentence_hits == 0
    assert verdict.attested is False
    assert verdict.note is not None
    # The BL-145 caveat must survive: absence of the string is not absence of the idea.
    assert "spelling" in verdict.note or "synonym" in verdict.note


def test_a_low_scanned_term_with_hits_needs_no_steering() -> None:
    """The silent-low convention: when the numbers answer, do not editorialize."""
    verdict = _verdict("Bene Gesserit", tf.SentenceScan(hits=12, capped=False))

    assert verdict.flood_risk == tf.LOW
    assert verdict.note is None


def test_a_moderate_scanned_term_warns_about_the_per_call_cap() -> None:
    verdict = _verdict("spice melange", tf.SentenceScan(hits=250, capped=False))

    assert verdict.flood_risk == tf.MODERATE
    assert verdict.note is not None
    assert "250" in verdict.note


# --- TEST-D2: the no-scan regression pin ------------------------------------


def test_a_compound_without_a_scan_keeps_the_unknown_verdict(recwarn) -> None:
    """FR-6: when the scan cannot serve a term, decline exactly as before.

    An honest decline beats a wrong measurement, and the BL-164 wording is the
    contract for that decline — pinned byte-for-byte.
    """
    scanned = _verdict("shared_buffers", None, counts={"shared": 400, "buffers": 90})

    assert scanned.method == tf.METHOD_LEXEME
    assert scanned.flood_risk == tf.UNKNOWN
    assert scanned.uses is None
    assert scanned.sentence_hits is None
    assert scanned.total_sentences is None
    assert scanned.capped is None
    assert scanned.note is not None
    assert "cannot be counted by the lexeme index" in scanned.note
    assert "search_sentences" in scanned.note


def test_a_phrase_without_a_scan_keeps_the_component_upper_bound() -> None:
    """Unchanged 1.51.0 behavior when no scan is supplied."""
    verdict = _verdict(
        "Model Context Protocol", None, counts={"model": 900, "context": 700, "protocol": 500}
    )

    assert verdict.method == tf.METHOD_LEXEME
    assert verdict.measurement == tf.COMPONENT_UPPER_BOUND
    assert verdict.uses == 500
    assert verdict.sentence_hits is None
    assert verdict.attested is None  # adjacency still unproven without a scan


# --- TEST-D3: single-word behavior is untouched -----------------------------


def test_an_exact_single_lexeme_is_unchanged_and_labelled() -> None:
    """NFR-3: the lexeme path keeps its numbers; it only gains a method label."""
    verdict = _verdict("spice", None, counts={"spice": 3493})

    assert verdict.method == tf.METHOD_LEXEME
    assert verdict.measurement == tf.EXACT
    assert verdict.uses == 3493
    assert verdict.attested is True
    assert verdict.sentence_hits is None
    assert verdict.total_sentences is None
    assert verdict.capped is None


def test_an_exact_single_lexeme_ignores_a_stray_scan() -> None:
    """A single lexeme is already measured exactly; the scan is not an upgrade.

    Scanning it would answer a different question (substring, not lemma) and
    silently change every existing single-word verdict.
    """
    verdict = _verdict("spice", tf.SentenceScan(hits=99999, capped=True), counts={"spice": 3493})

    assert verdict.method == tf.METHOD_LEXEME
    assert verdict.uses == 3493
    assert verdict.sentence_hits is None


# --- scan targeting ---------------------------------------------------------


def test_scan_keys_names_exactly_the_terms_the_scan_should_serve() -> None:
    """The resolver asks the adapter for these keys and no others."""
    prepared = tf.prepare_terms(["spice", "Model Context Protocol", "shared_buffers"])

    assert tf.scan_keys(prepared) == ["model context protocol", "shared_buffers"]


def test_scan_keys_is_empty_when_every_term_is_an_exact_lexeme() -> None:
    """No unindexable term means no sentence scan, so no second statement."""
    assert tf.scan_keys(tf.prepare_terms(["spice", "melange"])) == []


def test_scan_text_echoes_the_raw_spelling_the_substring_match_needs() -> None:
    """The scan matches raw text, not the normalized lexeme key.

    ``norm_lemma`` casefolds and NFC-normalizes; the literal-substring rule wants
    what the caller actually typed.
    """
    prepared = tf.prepare_terms(["Model Context Protocol"])

    assert tf.scan_text(prepared[0]) == "Model Context Protocol"
