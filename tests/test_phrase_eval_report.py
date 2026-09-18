"""TEST-U2 — the report generator: frontmatter, and provenance for every number.

The house rule this enforces is #47's NFR: *never report a surface number the
harness did not measure*. A benchmark report whose figures cannot be traced back
to the command that produced them is indistinguishable from a fabricated one,
which is exactly the failure mode PALS's Law names.

So the generator carries an executable definition of "provenanced", and this
module pins it: a bare number anywhere in the prose is a defect the generator
must be able to find itself.
"""

from __future__ import annotations

import _phrase_eval_criteria as crit
import _phrase_eval_report as report


def _run() -> report.EvalRun:
    measurements = [
        crit.ClassMeasurement(
            key=qc.key,
            p50_ms=12.5,
            p95_ms=41.0,
            runs=20,
            capped=False,
            false_positives=0,
            agreement=True,
            candidates=37,
        )
        for qc in crit.QUERY_CLASSES
    ]
    return report.EvalRun(
        generated_on="2026-07-28",
        leg="production",
        database_label="managed-postgres (read-only txn)",
        server_measurements=measurements,
        client_measurements=[],
        decision=crit.decide(measurements, lemma_requirement=False),
        price=crit.price_stage_two(
            token_rows=25_000_000,
            sentence_rows=870_000,
            distinct_surfaces=1_200_000,
            avg_sentence_chars=120.0,
        ),
        catalog_provenance="pg_class.reltuples via scripts/eval_phrase_retrieval.py --prod",
        plans=[("rare", "Index Scan using ... (actual time=0.1..0.2 rows=3)")],
        read_only_evidence="transaction_read_only = on; statement_timeout = 5s",
    )


# --- frontmatter (house rule: every generated .md carries the disclaimer) -----


def test_the_report_opens_with_yaml_frontmatter():
    text = report.render_report(_run())

    assert text.startswith("---\n")
    assert text.count("\n---\n") >= 1


def test_the_frontmatter_carries_the_mandated_disclaimer_fields():
    text = report.render_report(_run())
    head = text.split("\n---\n", 1)[0]

    assert "disclaimer:" in head
    assert "notice:" in head
    assert "generated_by:" in head
    assert "date:" in head
    assert "2026-07-28" in head


def test_the_report_names_the_issue_and_the_decision_claim():
    text = report.render_report(_run())

    assert "#47" in text
    assert crit.DECISION_CLAIM_ID in text


# --- provenance: no bare numbers (grep-checkable) ----------------------------


def test_a_rendered_report_has_no_unprovenanced_numbers():
    text = report.render_report(_run())

    assert report.find_unprovenanced_numbers(text) == []


def test_the_provenance_check_catches_a_planted_bare_number():
    """Guard the guard: a check that cannot fail proves nothing."""
    text = report.render_report(_run())
    planted = text + "\nThe candidate scan took 91 ms.\n"

    assert report.find_unprovenanced_numbers(planted) != []


def test_the_provenance_check_accepts_a_number_that_cites_its_source():
    text = report.render_report(_run())
    cited = text + "\nThe candidate scan took 91 ms (source: `eval_phrase_retrieval.py --prod`).\n"

    assert report.find_unprovenanced_numbers(cited) == []


def test_frontmatter_dates_are_not_treated_as_unprovenanced_prose():
    text = report.render_report(_run())

    assert "2026-07-28" in text
    assert report.find_unprovenanced_numbers(text) == []


# --- content: the numbers that justify the verdict must be present -----------


def test_every_measured_class_appears_in_the_latency_matrix():
    text = report.render_report(_run())

    for qc in crit.QUERY_CLASSES:
        assert qc.key in text


def test_the_matrix_reports_how_many_candidates_each_class_matched():
    """A latency without its candidate count cannot be interpreted."""
    text = report.render_report(_run())

    assert "Candidates" in text
    assert "37" in text


def test_the_report_states_the_verdict_and_its_rationale():
    run = _run()
    text = report.render_report(run)

    assert run.decision.verdict in text
    assert run.decision.rationale.split(".")[0] in text


def test_the_report_records_the_read_only_evidence():
    """FR/NFR-3: the PR must be able to show the prod leg wrote nothing."""
    text = report.render_report(_run())

    assert "transaction_read_only" in text


def test_the_report_labels_stage_two_numbers_as_estimates():
    text = report.render_report(_run())

    assert "estimate" in text.lower()


def test_the_report_includes_the_explain_transcripts():
    text = report.render_report(_run())

    assert "Index Scan using" in text


def test_a_timed_out_class_is_marked_as_a_floor_not_a_measurement():
    """`5000.0` from a timeout is not the same claim as `5000.0` from a run.

    Printed identically, a reader takes the first for a completed measurement.
    The row has to say the scan never finished.
    """
    run = _run()
    timed_out = crit.ClassMeasurement(
        key=crit.QUERY_CLASSES[0].key,
        p50_ms=5000.0,
        p95_ms=5000.0,
        runs=20,
        capped=False,
        false_positives=0,
        agreement=None,
        candidates=358,
        timed_out=True,
    )
    run = report.EvalRun(**{**vars(run), "server_measurements": [timed_out]})

    text = report.render_report(run)

    assert "timed out" in text.lower()
    assert "≥" in text or "floor" in text.lower()


def test_the_report_names_the_access_path_each_class_planned():
    """A latency without its access path cannot tell an operator what to fix."""
    run = _run()
    run = report.EvalRun(
        **{**vars(run), "plans": [("rare", "Limit\n  ->  Seq Scan on sentences s")]}
    )

    text = report.render_report(run)

    assert "Access path" in text
    assert "Seq Scan" in text


def test_the_report_flags_a_full_scan_of_the_fact_table():
    """A full scan is the finding, so the report must say so in words."""
    run = _run()
    run = report.EvalRun(
        **{**vars(run), "plans": [("rare", "Limit\n  ->  Seq Scan on sentences s")]}
    )

    text = report.render_report(run)

    assert "no index" in text.lower()


def test_a_run_without_stage_two_pricing_says_not_measured():
    """An unanalyzed catalog cannot be priced; zeros would read as a measurement."""
    run = _run()
    run = report.EvalRun(**{**vars(run), "price": None})

    text = report.render_report(run)

    assert "not measured" in text.lower()
    assert report.find_unprovenanced_numbers(text) == []


def test_a_run_without_plans_says_so_instead_of_omitting_the_section():
    """A silently missing section reads as "measured and fine"."""
    run = _run()
    run = report.EvalRun(**{**vars(run), "plans": []})

    text = report.render_report(run)

    assert "not measured" in text.lower() or "no plans" in text.lower()
