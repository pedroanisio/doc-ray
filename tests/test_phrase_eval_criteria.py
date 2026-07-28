"""TEST-U1 — the FR-5 go/no-go criteria, as pure functions over measured inputs.

Issue #47 (Retrieval · P8) gates the stage-2 positional engine (#48) on
*measurement*, not on judgement. The criteria therefore have to be a function
someone can audit and re-run, not a paragraph an agent interprets: given a
latency/correctness matrix, exactly one of GO / NO_GO / OPERATOR comes out.

These tests are written against fixture numbers so the rule is pinned
independently of whatever the live corpus happens to measure on the day.
"""

from __future__ import annotations

import _phrase_eval_criteria as crit
import pytest


def _m(
    key: str,
    *,
    p95: float = 100.0,
    p50: float = 50.0,
    runs: int = 20,
    capped: bool = False,
    false_positives: int = 0,
    agreement: bool | None = True,
    candidates: int = 10,
    timed_out: bool = False,
) -> crit.ClassMeasurement:
    return crit.ClassMeasurement(
        key=key,
        p50_ms=p50,
        p95_ms=p95,
        runs=runs,
        capped=capped,
        false_positives=false_positives,
        agreement=agreement,
        candidates=candidates,
        timed_out=timed_out,
    )


def _healthy() -> list[crit.ClassMeasurement]:
    return [_m(qc.key) for qc in crit.QUERY_CLASSES]


# --- the query-class table (FR-2) --------------------------------------------


def test_the_class_table_covers_every_class_fr2_names():
    """FR-2 enumerates seven classes; a missing one is an unmeasured claim."""
    assert {qc.kind for qc in crit.QUERY_CLASSES} == {
        "rare",
        "mid",
        "common_words",
        "all_short_words",
        "joiner",
        "confusable",
        "control",
    }


def test_the_class_table_carries_the_phrases_the_spec_named():
    phrases = {qc.phrase for qc in crit.QUERY_CLASSES}

    assert "Model Context Protocol" in phrases
    assert "algorithm design" in phrases
    assert "not happy" in phrases
    assert "of it" in phrases
    assert "ngram" in phrases


def test_exactly_one_class_is_the_tolerated_non_selective_one():
    """FR-5's NO-GO clause exempts *at most* the all-short-words class.

    If two classes claimed the exemption the rule would silently widen, so the
    table must name exactly one.
    """
    tolerated = [qc for qc in crit.QUERY_CLASSES if qc.tolerated_non_selective]

    assert len(tolerated) == 1
    assert tolerated[0].kind == "all_short_words"


def test_class_keys_are_unique():
    keys = [qc.key for qc in crit.QUERY_CLASSES]

    assert len(keys) == len(set(keys))


# --- percentiles: deterministic, nearest-rank (NFR-2) ------------------------


def test_percentiles_are_nearest_rank_and_deterministic():
    series = [float(n) for n in range(1, 101)]  # 1..100

    assert crit.percentile(series, 50) == 50.0
    assert crit.percentile(series, 95) == 95.0


def test_percentile_of_a_single_sample_is_that_sample():
    assert crit.percentile([7.5], 95) == 7.5


def test_percentile_ignores_input_order():
    assert crit.percentile([9.0, 1.0, 5.0], 50) == crit.percentile([1.0, 5.0, 9.0], 50)


def test_percentile_rejects_an_empty_series():
    """A percentile over nothing is a fabricated number (NFR-3 in spirit)."""
    with pytest.raises(ValueError):
        crit.percentile([], 95)


# --- FR-5: NO-GO (stay stage-1) ----------------------------------------------


def test_everything_within_budget_and_clean_is_a_no_go():
    decision = crit.decide(_healthy(), lemma_requirement=False)

    assert decision.verdict == crit.NO_GO


def test_only_the_all_short_words_class_over_budget_is_still_a_no_go():
    """FR-5 tolerates exactly this class: `of it` cannot be made selective."""
    measurements = [
        _m(qc.key, p95=900.0 if qc.tolerated_non_selective else 100.0) for qc in crit.QUERY_CLASSES
    ]

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.NO_GO


def test_a_capped_tolerated_class_does_not_force_a_go():
    """The tolerated class is *expected* to saturate its cap.

    Reading "any structural budget failure => GO" literally would make the
    NO-GO clause's explicit exemption unreachable, so the structural test skips
    the one class the exemption names.
    """
    measurements = [
        _m(qc.key, p95=900.0, capped=True) if qc.tolerated_non_selective else _m(qc.key)
        for qc in crit.QUERY_CLASSES
    ]

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.NO_GO


def test_a_truncated_agreement_check_does_not_dirty_the_correctness_suite():
    """`agreement=None` means "not comparable", not "disagreed".

    Above the shared 2001 candidate cap the two surfaces sample different
    subsets, so equality is not expected there and must not be scored as a
    correctness failure.
    """
    measurements = [_m(qc.key, agreement=None) for qc in crit.QUERY_CLASSES]

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.NO_GO


# --- FR-5: GO (build the positional engine) ----------------------------------


def test_a_structural_budget_failure_outside_the_tolerated_class_is_a_go():
    """Over budget *while already capped* is a failure caps cannot hide."""
    measurements = _healthy()
    measurements[0] = _m(measurements[0].key, p95=1200.0, capped=True)

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.GO
    assert measurements[0].key in decision.rationale


def test_a_class_that_could_not_finish_inside_the_timeout_is_a_structural_failure():
    """A statement timeout is a measurement, not an error to swallow.

    The candidate cap is a `LIMIT` on rows *returned*; it cannot shorten a scan
    that must read the whole relation to find them. A class that cannot complete
    inside the defensive bound is therefore non-selective in the way FR-5 calls
    structural.
    """
    measurements = _healthy()
    measurements[1] = _m(measurements[1].key, p95=5000.0, timed_out=True, candidates=0)

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.GO
    assert "timed out" in decision.rationale.lower()


def test_a_timed_out_tolerated_class_stays_tolerated():
    """`of it` timing out is the expected shape of the exempted class."""
    measurements = [
        _m(qc.key, p95=5000.0, timed_out=True) if qc.tolerated_non_selective else _m(qc.key)
        for qc in crit.QUERY_CLASSES
    ]

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.NO_GO


def test_an_operator_stated_lemma_requirement_is_a_go_on_its_own():
    """Stage 1 matches surfaces; lemma-phrase search is out of its reach."""
    decision = crit.decide(_healthy(), lemma_requirement=True)

    assert decision.verdict == crit.GO
    assert "lemma" in decision.rationale.lower()


# --- FR-5: anything in between -> the operator decides -----------------------


def test_over_budget_without_capping_is_an_operator_call_not_a_go():
    """Slow but not truncated is a tuning question, not a structural one."""
    measurements = _healthy()
    measurements[0] = _m(measurements[0].key, p95=1200.0, capped=False)

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.OPERATOR


def test_a_word_boundary_false_positive_blocks_the_no_go():
    """FR-1 requires zero boundary false positives; one is not "clean"."""
    measurements = _healthy()
    measurements[0] = _m(measurements[0].key, false_positives=1)

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.OPERATOR
    assert "false positive" in decision.rationale.lower()


def test_a_p6_p7_disagreement_blocks_the_no_go():
    measurements = _healthy()
    measurements[0] = _m(measurements[0].key, agreement=False)

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.OPERATOR
    assert "disagree" in decision.rationale.lower()


def test_a_corpus_where_nothing_matched_cannot_claim_a_no_go():
    """Fast because there was nothing to find is not fast because it is quick.

    Every class returning zero candidates means the corpus does not contain the
    benchmark phrases (or the session cannot see them). Sub-millisecond timings
    over an empty result set are not evidence that stage 1 meets its budget.
    """
    measurements = [_m(qc.key, p95=0.4, candidates=0) for qc in crit.QUERY_CLASSES]

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.OPERATOR
    assert "no candidates" in decision.rationale.lower()


def test_one_class_matching_nothing_is_normal_and_does_not_block_a_verdict():
    """A rare phrase legitimately being absent is data, not a broken run."""
    measurements = _healthy()
    measurements[0] = _m(measurements[0].key, candidates=0)

    decision = crit.decide(measurements, lemma_requirement=False)

    assert decision.verdict == crit.NO_GO


def test_decide_rejects_an_empty_matrix():
    """No measurements is not a NO-GO; it is nothing to decide on."""
    with pytest.raises(ValueError):
        crit.decide([], lemma_requirement=False)


def test_decide_rejects_a_matrix_missing_a_declared_class():
    """A partial run must not be able to produce a verdict."""
    with pytest.raises(ValueError):
        crit.decide(_healthy()[:-1], lemma_requirement=False)


# --- OD-2: escalate sample size only where it changes the answer -------------


def test_a_p95_near_the_budget_line_requires_the_larger_sample():
    """OD-2: escalate to 100 runs only for decision-relevant precision."""
    assert crit.needs_escalation(_m("x", p95=380.0, runs=20)) is True
    assert crit.needs_escalation(_m("x", p95=460.0, runs=20)) is True


def test_a_p95_far_from_the_budget_line_does_not_require_escalation():
    assert crit.needs_escalation(_m("x", p95=40.0, runs=20)) is False
    assert crit.needs_escalation(_m("x", p95=5000.0, runs=20)) is False


def test_an_already_escalated_class_is_not_escalated_again():
    assert crit.needs_escalation(_m("x", p95=380.0, runs=100)) is False


# --- FR-4: stage-2 pricing arithmetic is shown, not asserted -----------------


def test_stage_two_price_is_derived_from_the_measured_catalog_numbers():
    price = crit.price_stage_two(
        token_rows=25_000_000,
        sentence_rows=870_000,
        distinct_surfaces=1_200_000,
        avg_sentence_chars=120.0,
    )

    # index rows == token rows: one positional entry per token occurrence.
    assert price.index_rows == 25_000_000
    assert price.registry_rows == 1_200_000
    assert price.index_bytes > 0
    assert price.registry_bytes > 0


def test_stage_two_price_shows_its_arithmetic():
    """FR-4: "Show the arithmetic; label every estimate as an estimate"."""
    price = crit.price_stage_two(
        token_rows=25_000_000,
        sentence_rows=870_000,
        distinct_surfaces=1_200_000,
        avg_sentence_chars=120.0,
    )

    assert price.is_estimate is True
    assert "25000000" in price.arithmetic.replace(",", "").replace("_", "")
    assert str(crit.INDEX_ROW_BYTES) in price.arithmetic


def test_stage_two_price_rejects_impossible_catalog_input():
    with pytest.raises(ValueError):
        crit.price_stage_two(
            token_rows=-1,
            sentence_rows=870_000,
            distinct_surfaces=1_200_000,
            avg_sentence_chars=120.0,
        )
