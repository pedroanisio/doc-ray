"""Unit + integration tests for the MCP evaluation harness (issue #12).

The harness measures the agent-facing MCP tool surface instead of asserting it:
it runs grounded retrieval tasks against seeded fixtures and reports task
success, tool-call count, and payload size, so a change to a tool description or
return shape can be *shown* to help or hurt.

This module is two layers:

* **Unit (no DB)** — the pure scoring predicates in
  :mod:`doc_ray.domain.mcp_eval_scoring` over synthetic transcripts, budget
  accounting, the report serializer, and baseline comparison. Every predicate is
  exercised with a passing *and* a failing transcript so none passes vacuously.
* **Regression (no DB)** — the runner must fail a task *loudly* when a tool
  raises an unexpected ``ToolCallError`` rather than scoring it passed-with-zero
  -calls (a harness that reports success while the surface is broken is worse
  than no harness), and a task with an empty assertion tuple must be rejected at
  load.
* **Integration (DB-backed, Docker)** — seed one fixture document, run one task
  end to end through the real MCP tool callables + Strawberry schema, and produce
  a report. Skips cleanly without a safe test database, like every DB-backed
  suite.
"""

from __future__ import annotations

import asyncio

import pytest

from doc_ray.domain import mcp_eval_scoring as scoring
from doc_ray.domain.mcp_eval_scoring import RecordedCall, TaskBudget

# ---------------------------------------------------------------------------
# synthetic transcript builders
# ---------------------------------------------------------------------------


def _read(ordinals: list[int], *, chars: int = 100) -> RecordedCall:
    """A read_document_sentences call returning sentences at *ordinals*."""
    return RecordedCall(
        tool="read_document_sentences",
        args={"from_ordinal": ordinals[0] if ordinals else None},
        ok=True,
        response=[{"ordinal": o, "text": "x"} for o in ordinals],
        response_chars=chars,
    )


def _outline(total: int, *, chars: int = 80) -> RecordedCall:
    return RecordedCall(
        tool="read_document_outline",
        args={},
        ok=True,
        response={"source": "uniform", "totalSentences": total, "entries": []},
        response_chars=chars,
    )


def _search(ordinals: list[int], *, limit: int, note: str | None, chars: int = 200) -> RecordedCall:
    return RecordedCall(
        tool="search_sentences",
        args={"term": "spice"},
        ok=True,
        response={
            "hits": [{"ordinal": o, "hit": "spice"} for o in ordinals],
            "coverage": {
                "hit_count": len(ordinals),
                "ordinal_min": min(ordinals) if ordinals else None,
                "ordinal_max": max(ordinals) if ordinals else None,
                "limit": limit,
                "limit_reached": len(ordinals) >= limit,
            },
            "note": note,
        },
        response_chars=chars,
    )


# ---------------------------------------------------------------------------
# token / budget accounting
# ---------------------------------------------------------------------------


def test_token_estimate_is_a_labelled_char_ratio_not_a_measurement():
    # Open decision #1: characters are measured; tokens are a documented estimate
    # at CHARS_PER_TOKEN, never reported as a precise count.
    assert scoring.CHARS_PER_TOKEN == 4
    assert scoring.estimate_tokens(0) == 0
    assert scoring.estimate_tokens(400) == 100
    assert scoring.estimate_tokens(402) == 100  # rounded, not floored-with-false-precision


def test_budget_report_totals_calls_and_chars():
    calls = (_read([0, 1], chars=50), _read([2, 3], chars=70))
    rep = scoring.budget_report(calls, TaskBudget(max_calls=5, max_chars=200))
    assert rep.calls == 2
    assert rep.chars == 120
    assert rep.est_tokens == 30
    assert rep.within is True


def test_within_budget_fails_when_calls_or_chars_exceed_cap():
    calls = (_read([0], chars=500),)
    assert scoring.within_budget(calls, TaskBudget(max_calls=5, max_chars=200)).passed is False
    assert scoring.within_budget(calls, TaskBudget(max_calls=0, max_chars=9999)).passed is False
    assert scoring.within_budget(calls, TaskBudget(max_calls=5, max_chars=9999)).passed is True


# ---------------------------------------------------------------------------
# coverage predicate (cover-document-exhaustively)
# ---------------------------------------------------------------------------


def test_coverage_reaches_full_document_via_outline_total():
    calls = (_outline(4), _read([0, 1]), _read([2, 3]))
    res = scoring.check_coverage_at_least(calls, 0.99)
    assert res.passed is True
    assert "4/4" in res.detail or "1.00" in res.detail


def test_coverage_fails_when_back_half_is_never_read():
    # The exact failure the task guards: the reviewer ran out of budget on the
    # back half of a novel.
    calls = (_outline(4), _read([0, 1]))
    res = scoring.check_coverage_at_least(calls, 0.99)
    assert res.passed is False


def test_coverage_needs_an_outline_call_to_know_the_total():
    res = scoring.check_coverage_at_least((_read([0, 1]),), 0.5)
    assert res.passed is False
    assert "outline" in res.detail.lower()


# ---------------------------------------------------------------------------
# rare-term localisation (find-rare-scene)
# ---------------------------------------------------------------------------


def test_rare_term_hit_lands_in_the_expected_region():
    calls = (_search([812], limit=20, note=None),)
    assert scoring.check_search_hit_in_range(calls, 800, 820).passed is True


def test_rare_term_hit_outside_the_region_fails():
    calls = (_search([12], limit=20, note=None),)
    assert scoring.check_search_hit_in_range(calls, 800, 820).passed is False


def test_rare_term_with_no_hits_fails_rather_than_passing_empty():
    calls = (_search([], limit=20, note=None),)
    assert scoring.check_search_hit_in_range(calls, 800, 820).passed is False


# ---------------------------------------------------------------------------
# keyword flood (survive-keyword-flood)
# ---------------------------------------------------------------------------


def test_flood_is_flagged_when_limit_reached_with_a_note():
    flooded = _search(list(range(20)), limit=20, note="Returned the maximum 20 hits…")
    assert scoring.check_flood_flagged((flooded,)).passed is True


def test_flood_not_flagged_when_result_is_sparse():
    sparse = _search([1, 2, 3], limit=20, note=None)
    assert scoring.check_flood_flagged((sparse,)).passed is False


def test_content_is_reachable_after_a_flood_so_missing_is_wrong():
    # The agent that concluded "content is missing" would be wrong: a ranged read
    # of the untouched ordinals returns sentences.
    flooded = _search(list(range(20)), limit=20, note="flooded")
    recovered = _read([500, 501, 502])
    assert scoring.check_content_reachable_after_flood((flooded, recovered)).passed is True


def test_content_reachable_fails_if_the_followup_read_is_empty():
    flooded = _search(list(range(20)), limit=20, note="flooded")
    empty = RecordedCall(tool="read_document_sentences", args={}, ok=True, response=[])
    assert scoring.check_content_reachable_after_flood((flooded, empty)).passed is False


# ---------------------------------------------------------------------------
# read the ending (read-the-ending)
# ---------------------------------------------------------------------------


def test_reading_the_ending_addresses_the_tail_range_directly():
    calls = (_outline(1000), _read(list(range(980, 1000))))
    # last read starts at ordinal 980 -> in the last 5% tail of a 1000-sentence doc
    assert scoring.check_addresses_ending(calls, tail_fraction=0.1).passed is True


def test_reading_the_ending_fails_if_it_pages_from_the_start():
    calls = (_outline(1000), _read(list(range(0, 20))))
    assert scoring.check_addresses_ending(calls, tail_fraction=0.1).passed is False


# ---------------------------------------------------------------------------
# entity type split (entity-roster-not-fooled-by-type-split)
# ---------------------------------------------------------------------------


def _profiles(rows: list[dict]) -> RecordedCall:
    return RecordedCall(
        tool="entity_profiles", args={"document_id": "d"}, ok=True, response=rows, response_chars=90
    )


def test_type_split_entity_is_one_contested_row_not_four():
    row = {
        "surface": "Arrakis",
        "mentions": 546,
        "typeSpread": [
            {"entityType": "LOC", "count": 225},
            {"entityType": "GPE", "count": 150},
            {"entityType": "PERSON", "count": 95},
            {"entityType": "NORP", "count": 76},
        ],
        "typeConsistency": 0.41,
    }
    res = scoring.check_single_contested_profile((_profiles([row]),), "Arrakis", min_types=2)
    assert res.passed is True


def test_type_split_fails_when_the_entity_is_split_into_many_rows():
    rows = [
        {
            "surface": "Arrakis",
            "mentions": 225,
            "typeSpread": [{"entityType": "LOC", "count": 225}],
            "typeConsistency": 1.0,
        },
        {
            "surface": "Arrakis",
            "mentions": 150,
            "typeSpread": [{"entityType": "GPE", "count": 150}],
            "typeConsistency": 1.0,
        },
    ]
    res = scoring.check_single_contested_profile((_profiles(rows),), "Arrakis", min_types=2)
    assert res.passed is False


def test_type_split_fails_when_the_single_row_is_not_flagged_contested():
    row = {
        "surface": "Arrakis",
        "mentions": 546,
        "typeSpread": [{"entityType": "LOC", "count": 546}],
        "typeConsistency": 1.0,
    }
    res = scoring.check_single_contested_profile((_profiles([row]),), "Arrakis", min_types=2)
    assert res.passed is False


# ---------------------------------------------------------------------------
# cursor dead-end (cursor-not-interchangeable)
# ---------------------------------------------------------------------------


def _cursor_error() -> RecordedCall:
    return RecordedCall(
        tool="read_document_sentences",
        args={"after": "eyJ...sentence-search..."},
        ok=False,
        error_kind="bad_input",
        error_hint=(
            "cursors are not interchangeable between tools — a search_sentences cursor is "
            "not valid here. Page read_document_sentences with its own cursor, or address a "
            "slice directly with from_ordinal/to_ordinal."
        ),
    )


def test_cursor_dead_end_is_steered_not_a_bare_error():
    assert scoring.check_cursor_dead_end_steered((_cursor_error(),)).passed is True


def test_cursor_dead_end_unsteered_error_fails_the_check():
    bare = RecordedCall(
        tool="read_document_sentences",
        args={},
        ok=False,
        error_kind="upstream_error",
        error_hint=None,
    )
    assert scoring.check_cursor_dead_end_steered((bare,)).passed is False


def test_recovery_after_cursor_dead_end_within_one_extra_call():
    calls = (_cursor_error(), _read([0, 1, 2]))
    assert scoring.check_recovered_after_cursor(calls, extra_calls=1).passed is True


def test_recovery_fails_if_no_successful_read_follows():
    assert scoring.check_recovered_after_cursor((_cursor_error(),), extra_calls=1).passed is False


# ---------------------------------------------------------------------------
# generic reachability predicates (coverage tasks / drift guard)
# ---------------------------------------------------------------------------


def test_tool_ok_passes_even_on_a_well_formed_empty_result():
    empty_visual = RecordedCall(
        tool="document_images", args={"document_id": "d"}, ok=True, response=[]
    )
    assert scoring.check_tool_ok((empty_visual,), "document_images").passed is True


def test_tool_ok_fails_when_the_tool_raised_or_was_never_called():
    raised = RecordedCall(tool="get_document", args={}, ok=False, error_kind="not_found")
    assert scoring.check_tool_ok((raised,), "get_document").passed is False
    assert scoring.check_tool_ok((), "get_document").passed is False


def test_tool_nonempty_distinguishes_content_from_empty():
    full = RecordedCall(tool="list_lenses", args={}, ok=True, response=[{"name": "global"}])
    empty = RecordedCall(tool="list_lenses", args={}, ok=True, response=[])
    assert scoring.check_tool_nonempty((full,), "list_lenses").passed is True
    assert scoring.check_tool_nonempty((empty,), "list_lenses").passed is False


def test_all_ok_fails_if_any_call_raised():
    ok = RecordedCall(tool="visual_captions", args={}, ok=True, response=[])
    bad = RecordedCall(tool="image_class_summary", args={}, ok=False, error_kind="upstream_error")
    assert scoring.check_all_ok((ok, ok)).passed is True
    assert scoring.check_all_ok((ok, bad)).passed is False


def test_tool_reachable_accepts_a_documented_unsupported_outcome():
    # A deployment-optional tool (e.g. semantic search without the model stack)
    # correctly returns kind "unsupported"; that is reachable, not a failure.
    ok = RecordedCall(tool="semantic_search_sentences", args={}, ok=True, response=[])
    unsupported = RecordedCall(
        tool="semantic_search_sentences", args={}, ok=False, error_kind="unsupported"
    )
    other = RecordedCall(
        tool="semantic_search_sentences", args={}, ok=False, error_kind="upstream_error"
    )
    assert scoring.check_tool_reachable((ok,), "semantic_search_sentences").passed is True
    assert scoring.check_tool_reachable((unsupported,), "semantic_search_sentences").passed is True
    assert scoring.check_tool_reachable((other,), "semantic_search_sentences").passed is False
    assert scoring.check_tool_reachable((), "semantic_search_sentences").passed is False


# ---------------------------------------------------------------------------
# report serialization + baseline comparison
# ---------------------------------------------------------------------------


def _passing_results() -> tuple[scoring.AssertionResult, ...]:
    return (scoring.AssertionResult("a", True, ""), scoring.AssertionResult("b", True, ""))


def test_task_report_shape_is_machine_readable_and_deterministic():
    calls = (_outline(4, chars=80), _read([0, 1], chars=60))
    rep = scoring.task_report(
        task_id="cover-document-exhaustively",
        grounded_in="reviewer ran out of budget",
        calls=calls,
        results=_passing_results(),
        budget=TaskBudget(max_calls=5, max_chars=500),
    )
    assert rep["task_id"] == "cover-document-exhaustively"
    assert rep["passed"] is True
    assert rep["calls"] == 2
    assert rep["chars"] == 140
    assert rep["est_tokens"] == 35
    assert rep["within_budget"] is True
    assert [a["name"] for a in rep["assertions"]] == ["a", "b"]
    # No wall-clock in the deterministic body (it lives only at run level).
    assert "duration_s" not in rep


def test_task_report_fails_when_any_assertion_fails():
    calls = (_read([0]),)
    results = (scoring.AssertionResult("a", True, ""), scoring.AssertionResult("b", False, "nope"))
    rep = scoring.task_report("t", "g", calls, results, TaskBudget(9, 9999))
    assert rep["passed"] is False


def test_aggregate_counts_pass_fail_and_totals():
    good = scoring.task_report(
        "t1", "g", (_read([0], chars=10),), _passing_results(), TaskBudget(9, 9999)
    )
    bad = scoring.task_report(
        "t2",
        "g",
        (_read([0], chars=20),),
        (scoring.AssertionResult("x", False, ""),),
        TaskBudget(9, 9999),
    )
    agg = scoring.aggregate([good, bad])
    assert agg["tasks"] == 2
    assert agg["passed"] == 1
    assert agg["failed"] == 1
    assert agg["total_calls"] == 2
    assert agg["total_chars"] == 30


def test_render_table_is_human_readable_and_names_every_task():
    good = scoring.task_report(
        "cover-document", "g", (_read([0]),), _passing_results(), TaskBudget(9, 9999)
    )
    table = scoring.render_table(scoring.build_report(mode="scripted", task_reports=[good]))
    assert "cover-document" in table
    assert "PASS" in table


def test_baseline_comparison_flags_a_regression_beyond_tolerance():
    base = scoring.build_report(
        mode="scripted",
        task_reports=[
            scoring.task_report(
                "t", "g", (_read([0], chars=100),), _passing_results(), TaskBudget(9, 9999)
            )
        ],
    )
    # A run where the same task now costs many more chars, and one assertion regressed.
    worse = scoring.build_report(
        mode="scripted",
        task_reports=[
            scoring.task_report(
                "t",
                "g",
                (_read([0], chars=100), _read([1], chars=400)),
                (scoring.AssertionResult("x", False, ""),),
                TaskBudget(9, 9999),
            )
        ],
    )
    cmp = scoring.compare(worse, base, char_tolerance=0.1, call_tolerance=0)
    assert cmp["regressed"] is True
    assert any("t" in r for r in cmp["reasons"])


def test_baseline_comparison_is_clean_when_metrics_match():
    rep = scoring.build_report(
        mode="scripted",
        task_reports=[
            scoring.task_report(
                "t", "g", (_read([0], chars=100),), _passing_results(), TaskBudget(9, 9999)
            )
        ],
    )
    cmp = scoring.compare(rep, rep, char_tolerance=0.1, call_tolerance=0)
    assert cmp["regressed"] is False
    assert cmp["reasons"] == []


# ---------------------------------------------------------------------------
# runner: fail loudly, and reject malformed tasks (regression + validation)
# ---------------------------------------------------------------------------

from doc_ray.application import mcp_eval  # noqa: E402
from doc_ray.application.mcp_eval import Assertion, EvalTask, RunContext, Step  # noqa: E402
from doc_ray.domain.mcp_eval_scoring import AssertionResult  # noqa: E402
from doc_ray.entrypoints.mcp.corpus import ToolCallError  # noqa: E402


async def _stub_ok(**kwargs: object) -> dict[str, bool]:
    return {"ok": True}


async def _stub_raises(**kwargs: object) -> dict[str, bool]:
    raise ToolCallError("upstream_error", "the tool is broken")


def _one_step_task(tool: object, *, expect_error: bool = False, assertions: tuple = ()) -> EvalTask:
    return EvalTask(
        id="t",
        kind="grounded",
        prompt="p",
        grounded_in="g",
        steps=(Step(tool, lambda ctx: {}, expect_error=expect_error),),
        assertions=assertions
        or (Assertion("always", lambda calls: AssertionResult("always", True)),),
        budget=TaskBudget(max_calls=9, max_chars=99999),
    )


def test_harness_fails_loudly_when_a_tool_raises_unexpectedly():
    # A harness that scored a broken tool as passed-with-zero-calls would be worse
    # than no harness. The unexpected raise must surface as a FAILED task.
    task = _one_step_task(_stub_raises)
    report = asyncio.run(mcp_eval.score_task(task, RunContext(ids={})))
    assert report["passed"] is False
    assert any(a["name"] == "no_unexpected_tool_error" for a in report["assertions"])
    assert report["calls"] == 1  # the failed call is recorded, not swallowed


def test_expected_error_step_is_scored_normally_not_as_a_harness_failure():
    # The cursor task deliberately provokes a ToolCallError; that is data, not a fault.
    task = _one_step_task(_stub_raises, expect_error=True)
    report = asyncio.run(mcp_eval.score_task(task, RunContext(ids={})))
    assert report["passed"] is True


def test_task_with_empty_assertion_tuple_is_rejected_at_load():
    task = EvalTask(
        id="empty",
        kind="grounded",
        prompt="p",
        grounded_in="g",
        steps=(Step(_stub_ok, lambda ctx: {}),),
        assertions=(),
        budget=TaskBudget(1, 1),
    )
    with pytest.raises(ValueError, match="empty assertion tuple"):
        mcp_eval.validate_task(task)


def test_agentic_mode_is_a_typed_unsupported_error_naming_the_missing_config():
    err = mcp_eval.unsupported_agentic()
    assert isinstance(err, mcp_eval.EvalModeUnsupported)
    assert "agentic" in str(err) and "config/env.registry.yaml" in str(err)


# ---------------------------------------------------------------------------
# integration (DB-backed, Docker): seed fixtures, run tasks end to end
# ---------------------------------------------------------------------------


def _seed_world(pg):
    from doc_ray.application import mcp_eval_fixtures as fx

    conn, _created = pg
    return conn, fx.seed_world(conn, mcp_eval.HARNESS_PRINCIPAL_ID)


def test_one_task_runs_end_to_end_and_produces_a_report(pg):
    from doc_ray.application import mcp_eval_fixtures as fx
    from doc_ray.application.mcp_eval_tasks import TASKS

    conn, world = _seed_world(pg)
    try:
        cover = next(t for t in TASKS if t.id == "cover-document-exhaustively")
        reports = asyncio.run(mcp_eval.run_scripted((cover,), world))
        rep = reports[0]
        assert rep["task_id"] == "cover-document-exhaustively"
        assert rep["passed"] is True, rep["assertions"]
        assert rep["calls"] >= 3 and rep["chars"] > 0
    finally:
        fx.cleanup_world(conn, world)


def test_full_scripted_run_passes_every_task(pg):
    from doc_ray.application import mcp_eval_fixtures as fx
    from doc_ray.application.mcp_eval_tasks import TASKS

    conn, world = _seed_world(pg)
    try:
        reports = asyncio.run(mcp_eval.run_scripted(TASKS, world))
        failed = [t["task_id"] for t in reports if not t["passed"]]
        assert not failed, {t["task_id"]: t["assertions"] for t in reports if not t["passed"]}
    finally:
        fx.cleanup_world(conn, world)


def test_two_independent_seedings_produce_identical_reports(pg):
    # FR-4: same task set + same tool surface -> identical scripted metrics, even
    # across *independent* seed cycles (fresh ids/timestamps). This is the guard
    # that catches payload-length drift from volatile fields like ``createdAt`` —
    # a same-world double-run would not, because it shares one seeding.
    from doc_ray.application import mcp_eval_fixtures as fx
    from doc_ray.application.mcp_eval_tasks import TASKS

    conn, _created = pg
    world_a = fx.seed_world(conn, mcp_eval.HARNESS_PRINCIPAL_ID)
    try:
        report_a = asyncio.run(mcp_eval.run_scripted(TASKS, world_a))
    finally:
        fx.cleanup_world(conn, world_a)
    world_b = fx.seed_world(conn, mcp_eval.HARNESS_PRINCIPAL_ID)
    try:
        report_b = asyncio.run(mcp_eval.run_scripted(TASKS, world_b))
    finally:
        fx.cleanup_world(conn, world_b)
    assert report_a == report_b
