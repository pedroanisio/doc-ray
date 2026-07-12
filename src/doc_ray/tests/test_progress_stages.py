"""BL-123 gap: within-job progress must ADVANCE, not sit at a single stage.

The first landing reported one coarse ``0/1`` stage per handler, so a
248-page annotate showed a frozen 0% for its whole runtime. These tests pin
the fix: the reporter lives in ``domain.progress`` (importable from adapters
without breaking the hexagonal import direction), the spaCy annotator emits
advancing phase boundaries, and semantic enrichment reports per layer.
"""

from __future__ import annotations

import pytest

from doc_ray.domain import progress as domain_progress


def _capture() -> tuple[list[tuple[int | None, int | None, str | None]], object]:
    events: list[tuple[int | None, int | None, str | None]] = []

    def reporter(current: int | None, total: int | None, stage: str | None) -> None:
        events.append((current, total, stage))

    token = domain_progress.set_progress_reporter(reporter)
    return events, token


def test_domain_reporter_forwards_and_is_noop_when_unset():
    events, token = _capture()
    try:
        domain_progress.report_progress(1, 4, "half way there")
        assert events == [(1, 4, "half way there")]
    finally:
        domain_progress.reset_progress_reporter(token)
    domain_progress.report_progress(2, 4, "dropped")
    assert events == [(1, 4, "half way there")]


def test_application_jobs_reexports_the_domain_reporter():
    from doc_ray.application import jobs

    events, token = _capture()
    try:
        jobs.report_progress(3, 7, "compat")
    finally:
        domain_progress.reset_progress_reporter(token)
    assert events == [(3, 7, "compat")]


def test_spacy_annotate_reports_advancing_phases(pg):
    import _itdb

    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    conn, created = pg
    doc_id = _itdb.make_doc(
        conn,
        "Elena founded a startup in Berlin. The company grew quickly. "
        "Investors from Paris joined the board in March.",
    )
    created.append(doc_id)

    events, token = _capture()
    try:
        SpacyAnnotator().annotate(conn, doc_id, lang_default="en")
    finally:
        domain_progress.reset_progress_reporter(token)

    assert events, "annotate() reported no progress at all"
    currents = [c for c, _t, _s in events if c is not None]
    totals = {t for _c, t, _s in events if t is not None}
    stages = {s for _c, _t, s in events if s}
    assert currents == sorted(currents), f"progress went backwards: {currents}"
    assert totals == {7}, f"expected a stable 7-phase scale, got {totals}"
    assert len(stages) >= 5, f"expected distinct phase stages, got {stages}"
    assert currents[0] == 0
    assert currents[-1] >= 6, "annotate() never reached its persistence phase"


def test_semantic_enrichment_reports_per_layer_progress(pg):
    import _itdb

    from doc_ray.application import semantic_enrichment

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "It rained on Tuesday. The launch was delayed.")
    created.append(doc_id)

    events, token = _capture()
    try:
        semantic_enrichment.annotate_document(conn, doc_id)
    finally:
        domain_progress.reset_progress_reporter(token)

    layered = [(c, t, s) for c, t, s in events if t == 5]
    assert len(layered) >= 5, f"expected 5 layer reports on a /5 scale, got {events}"
    layer_currents = [c for c, _t, _s in layered if c is not None]
    assert layer_currents == sorted(layer_currents)
    assert layer_currents[0] == 0 and layer_currents[-1] == 4


def test_adapter_does_not_import_application_layer():
    """The instrumentation must not break the hexagonal import direction."""
    import ast
    from pathlib import Path

    source = Path("src/doc_ray/adapters/annotation_spacy.py").read_text(encoding="utf-8")
    offenders = [
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("doc_ray.application")
    ]
    assert not offenders, f"adapter imports application modules: {offenders}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
