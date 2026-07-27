"""Pure scoring for the MCP evaluation harness (issue #12).

The harness records what the agent-facing MCP tools returned for a scripted
sequence of calls, then scores that transcript. Everything here is a **pure
function over the recorded transcript** — no database, no network, no clock — so
it is unit-testable in isolation and produces the same numbers for the same
input. That determinism is the point: it is what lets a change to a tool
description or return shape be *shown* to help or hurt rather than merely
asserted to (the failure mode issue #12 closes).

The split mirrors ``domain/document_outline.py`` / ``domain/entity_profiles.py``:
the adapter (here, the runner in ``application/mcp_eval.py``) supplies the rows —
the recorded calls — and the domain decides pass/fail. Token counts are a
**documented estimate** at :data:`CHARS_PER_TOKEN`, never a measurement (Open
decision #1): characters are counted exactly; tokens are ``chars / 4`` so a
precise-looking wrong number is never reported.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: Characters per token — a documented estimate, not a tokenizer. doc-ray avoids
#: an unnecessary tokenizer dependency (Open decision #1); reporting an estimate
#: is honest where a precise-looking wrong count is not.
CHARS_PER_TOKEN = 4


def estimate_tokens(chars: int) -> int:
    """Estimate tokens from characters at :data:`CHARS_PER_TOKEN` (rounded)."""
    return round(chars / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class RecordedCall:
    """One tool call the harness made, and what came back.

    ``response_chars`` is the exact size of the payload the agent would see and is
    deterministic; ``duration_s`` is wall-clock and therefore excluded from every
    deterministic metric and comparison.
    """

    tool: str
    args: dict[str, Any]
    ok: bool
    response: Any = None
    response_chars: int = 0
    error_kind: str | None = None
    error_hint: str | None = None
    duration_s: float = 0.0


@dataclass(frozen=True)
class AssertionResult:
    """The outcome of one scoring predicate against a transcript."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class TaskBudget:
    """The ceiling a task must stay under to count as an efficient success."""

    max_calls: int
    max_chars: int


@dataclass(frozen=True)
class BudgetReport:
    """A task's measured cost against its budget (all fields deterministic)."""

    calls: int
    chars: int
    est_tokens: int
    max_calls: int
    max_chars: int
    within: bool


Calls = tuple[RecordedCall, ...]


# ---------------------------------------------------------------------------
# transcript accessors (pure)
# ---------------------------------------------------------------------------


def _reads(calls: Calls) -> list[RecordedCall]:
    return [
        c
        for c in calls
        if c.tool == "read_document_sentences" and c.ok and isinstance(c.response, list)
    ]


def _searches(calls: Calls) -> list[RecordedCall]:
    return [
        c for c in calls if c.tool == "search_sentences" and c.ok and isinstance(c.response, dict)
    ]


def _read_ordinals(call: RecordedCall) -> set[int]:
    return {
        s["ordinal"]
        for s in call.response
        if isinstance(s, dict) and isinstance(s.get("ordinal"), int)
    }


def ordinals_read(calls: Calls) -> set[int]:
    """Union of sentence ordinals returned by every ranged read in the transcript."""
    out: set[int] = set()
    for call in _reads(calls):
        out |= _read_ordinals(call)
    return out


def outline_total(calls: Calls) -> int | None:
    """The ``totalSentences`` reported by a ``read_document_outline`` call, if any."""
    for call in calls:
        if call.tool == "read_document_outline" and call.ok and isinstance(call.response, dict):
            total = call.response.get("totalSentences")
            if isinstance(total, int):
                return total
    return None


# ---------------------------------------------------------------------------
# budget accounting (pure)
# ---------------------------------------------------------------------------


def total_calls(calls: Calls) -> int:
    """The number of tool calls in the transcript."""
    return len(calls)


def total_response_chars(calls: Calls) -> int:
    """The exact total size, in characters, of every payload the agent saw."""
    return sum(c.response_chars for c in calls)


def budget_report(calls: Calls, budget: TaskBudget) -> BudgetReport:
    """Total the transcript's calls and characters and test them against *budget*."""
    chars = total_response_chars(calls)
    within = len(calls) <= budget.max_calls and chars <= budget.max_chars
    return BudgetReport(
        calls=len(calls),
        chars=chars,
        est_tokens=estimate_tokens(chars),
        max_calls=budget.max_calls,
        max_chars=budget.max_chars,
        within=within,
    )


def within_budget(calls: Calls, budget: TaskBudget) -> AssertionResult:
    """Pass when the transcript stays within both the call and character ceilings."""
    rep = budget_report(calls, budget)
    return AssertionResult(
        "within_budget",
        rep.within,
        f"{rep.calls}/{budget.max_calls} calls, {rep.chars}/{budget.max_chars} chars",
    )


# ---------------------------------------------------------------------------
# scoring predicates (pure) — each grounded in a real observed failure
# ---------------------------------------------------------------------------


def check_coverage_at_least(calls: Calls, min_fraction: float) -> AssertionResult:
    """The ranged reads cover at least *min_fraction* of the document's sentences.

    Grounds the ``cover-document-exhaustively`` task: the original reviewer ran
    out of budget on the back half of a novel. The total comes from the outline
    call, so this is fully transcript-pure.
    """
    total = outline_total(calls)
    if total is None:
        return AssertionResult(
            "coverage", False, "no read_document_outline call to establish the total"
        )
    if total <= 0:
        return AssertionResult("coverage", False, "outline reported zero sentences")
    covered = {o for o in ordinals_read(calls) if 0 <= o < total}
    fraction = len(covered) / total
    return AssertionResult(
        "coverage",
        fraction >= min_fraction,
        f"{len(covered)}/{total} sentences ({fraction:.2f}) vs required {min_fraction:.2f}",
    )


def check_search_hit_in_range(calls: Calls, lo: int, hi: int) -> AssertionResult:
    """A rare, scene-specific term localises: every hit lands in the ordinal window.

    Grounds ``find-rare-scene`` (the Item 1 rare-term guidance). An empty result
    fails rather than passing vacuously.
    """
    searches = _searches(calls)
    if not searches:
        return AssertionResult("hit_in_range", False, "no search_sentences call")
    hits = searches[0].response.get("hits") or []
    ordinals = [h["ordinal"] for h in hits if isinstance(h.get("ordinal"), int)]
    if not ordinals:
        return AssertionResult("hit_in_range", False, "the rare term found nothing")
    inside = all(lo <= o <= hi for o in ordinals)
    return AssertionResult(
        "hit_in_range",
        inside,
        f"hits at ordinals {min(ordinals)}–{max(ordinals)} vs expected [{lo},{hi}]",
    )


def check_flood_flagged(calls: Calls) -> AssertionResult:
    """A high-frequency term is reported as flooded (``limit_reached`` + a steer note).

    Grounds ``survive-keyword-flood``: the surface must make a flood legible so a
    sparse-looking result reads as a *query* problem, not missing content.
    """
    for call in _searches(calls):
        coverage = call.response.get("coverage") or {}
        if coverage.get("limit_reached") and call.response.get("note"):
            return AssertionResult(
                "flood_flagged", True, "coverage.limit_reached with a steer note"
            )
    return AssertionResult(
        "flood_flagged", False, "no search flagged coverage.limit_reached with a note"
    )


def check_content_reachable_after_flood(calls: Calls) -> AssertionResult:
    """After a flood, a ranged read of untouched ordinals returns content.

    The agent that concluded "content is missing" would be wrong — the harness
    proves the content was reachable all along, so the flood was a retrieval
    problem. Requires the flood *then* a non-empty read after it.
    """
    flood_at = next(
        (
            i
            for i, c in enumerate(calls)
            if c.tool == "search_sentences"
            and c.ok
            and isinstance(c.response, dict)
            and (c.response.get("coverage") or {}).get("limit_reached")
        ),
        None,
    )
    if flood_at is None:
        return AssertionResult("content_reachable", False, "no flooded search to recover from")
    for call in calls[flood_at + 1 :]:
        if call.tool == "read_document_sentences" and call.ok and call.response:
            return AssertionResult(
                "content_reachable", True, f"{len(call.response)} sentences read past the flood"
            )
    return AssertionResult(
        "content_reachable", False, "no non-empty ranged read followed the flood"
    )


def check_addresses_ending(calls: Calls, tail_fraction: float) -> AssertionResult:
    """The document's ending is addressed directly, not paged from ordinal 0.

    Grounds ``read-the-ending`` (Item 5's reason for existing): the last ranged
    read must land in the final *tail_fraction* of the document.
    """
    total = outline_total(calls)
    if total is None or total <= 0:
        return AssertionResult("addresses_ending", False, "no outline to locate the tail")
    reads = _reads(calls)
    if not reads:
        return AssertionResult("addresses_ending", False, "no ranged read")
    last = reads[-1]
    ordinals = _read_ordinals(last)
    if not ordinals:
        return AssertionResult("addresses_ending", False, "the final read returned nothing")
    tail_start = total * (1 - tail_fraction)
    started_in_tail = min(ordinals) >= tail_start
    return AssertionResult(
        "addresses_ending",
        started_in_tail,
        f"final read starts at ordinal {min(ordinals)} vs tail start {tail_start:.0f}/{total}",
    )


def check_single_contested_profile(calls: Calls, surface: str, min_types: int) -> AssertionResult:
    """A type-split entity is one reconciled, contested row — not one row per type.

    Grounds ``entity-roster-not-fooled-by-type-split`` (Item 6's reason): exactly
    one profile row for *surface*, spanning at least *min_types* types, flagged
    contested (``typeConsistency`` < 1).
    """
    profile_calls = [
        c for c in calls if c.tool == "entity_profiles" and c.ok and isinstance(c.response, list)
    ]
    if not profile_calls:
        return AssertionResult("one_contested_profile", False, "no entity_profiles call")
    rows = [
        r for r in profile_calls[-1].response if isinstance(r, dict) and r.get("surface") == surface
    ]
    if len(rows) != 1:
        return AssertionResult(
            "one_contested_profile", False, f"{len(rows)} rows for {surface!r}, expected exactly 1"
        )
    row = rows[0]
    spread = row.get("typeSpread") or []
    consistency = row.get("typeConsistency")
    contested = isinstance(consistency, (int, float)) and consistency < 1.0
    passed = len(spread) >= min_types and contested
    return AssertionResult(
        "one_contested_profile",
        passed,
        f"1 row, {len(spread)} types, typeConsistency={consistency}",
    )


def check_cursor_dead_end_steered(calls: Calls) -> AssertionResult:
    """Handing a ``search_sentences`` cursor to the reader is steered, not a bare error.

    Grounds ``cursor-not-interchangeable`` (the cursor dead-end in BL-145): the
    failed call must carry the "cursors are not interchangeable" recovery hint.
    """
    for call in calls:
        if not call.ok and call.error_hint:
            hint = call.error_hint.lower()
            if "cursor" in hint and ("interchangeable" in hint or "not valid here" in hint):
                return AssertionResult(
                    "cursor_steered", True, "reader rejected the search cursor with a recovery hint"
                )
    return AssertionResult("cursor_steered", False, "no steered cursor error in the transcript")


def check_recovered_after_cursor(calls: Calls, extra_calls: int) -> AssertionResult:
    """After the cursor dead-end, a ranged read recovers within *extra_calls* calls."""
    dead_end_at = next(
        (
            i
            for i, c in enumerate(calls)
            if not c.ok and c.error_hint and "cursor" in c.error_hint.lower()
        ),
        None,
    )
    if dead_end_at is None:
        return AssertionResult("cursor_recovered", False, "no cursor dead-end to recover from")
    window = calls[dead_end_at + 1 : dead_end_at + 1 + extra_calls]
    for call in window:
        if call.tool == "read_document_sentences" and call.ok and call.response:
            return AssertionResult(
                "cursor_recovered", True, f"recovered via ordinals within {extra_calls} call(s)"
            )
    return AssertionResult(
        "cursor_recovered",
        False,
        f"no successful read within {extra_calls} call(s) of the dead-end",
    )


# --- generic reachability predicates (the "coverage" tasks, drift guard) -----


def _is_nonempty(response: object) -> bool:
    if isinstance(response, (list, dict, str)):
        return len(response) > 0
    return response is not None


def check_phrase_measured(calls: Calls, present: str, absent: str) -> AssertionResult:
    """A multi-word pre-flight returns a MEASURED verdict, not a declined one.

    Grounds the phrase half of the flood-risk pre-flight: the whole point of
    asking before searching is to get an answer. ``unknown`` for a phrase sends the
    agent to spend the very search call the pre-flight exists to save.
    """
    rows = [row for call in calls if call.tool == "term_frequency" for row in call.response or []]
    if not rows:
        return AssertionResult("phrase_measured", False, "term_frequency returned no rows")
    by_term = {str(row.get("term", "")).casefold(): row for row in rows}
    hit = by_term.get(present.casefold())
    miss = by_term.get(absent.casefold())
    if hit is None or miss is None:
        return AssertionResult("phrase_measured", False, "phrase rows missing from the response")
    if hit.get("method") != "sentence_scan" or hit.get("floodRisk") == "unknown":
        return AssertionResult(
            "phrase_measured", False, f"present phrase declined: {hit.get('method')!r}"
        )
    if not (hit.get("sentenceHits") or 0) > 0:
        return AssertionResult("phrase_measured", False, "present phrase measured zero hits")
    if miss.get("sentenceHits") != 0 or miss.get("attested") is not False:
        return AssertionResult(
            "phrase_measured", False, f"absent phrase not a measured zero: {miss.get('attested')!r}"
        )
    return AssertionResult(
        "phrase_measured", True, f"{hit.get('sentenceHits')} hits measured, absent phrase = 0"
    )


def check_tool_ok(calls: Calls, tool: str) -> AssertionResult:
    """The named tool was called and returned without a ``ToolCallError``.

    Reachability coverage: proves the tool's argument validation, GraphQL
    document, and response shaping all run against the live schema — even when the
    fixture legitimately yields an empty result (e.g. a document with no images).
    """
    called = [c for c in calls if c.tool == tool]
    if not called:
        return AssertionResult(f"{tool}_ok", False, "tool was never called")
    failed = [c for c in called if not c.ok]
    if failed:
        return AssertionResult(f"{tool}_ok", False, f"raised [{failed[0].error_kind}]")
    return AssertionResult(f"{tool}_ok", True, f"{len(called)} call(s), all ok")


def check_tool_nonempty(calls: Calls, tool: str) -> AssertionResult:
    """The named tool returned a non-empty payload (stronger than reachability)."""
    called = [c for c in calls if c.tool == tool and c.ok]
    if not called:
        return AssertionResult(f"{tool}_nonempty", False, "tool was never called successfully")
    ok = any(_is_nonempty(c.response) for c in called)
    return AssertionResult(f"{tool}_nonempty", ok, "returned content" if ok else "returned empty")


def check_all_ok(calls: Calls) -> AssertionResult:
    """Every call in the transcript succeeded — no tool raised."""
    failed = [c for c in calls if not c.ok]
    if failed:
        return AssertionResult(
            "all_ok", False, f"{len(failed)} call(s) raised, first: {failed[0].tool}"
        )
    return AssertionResult("all_ok", True, f"all {len(calls)} call(s) ok")


def check_tool_reachable(
    calls: Calls, tool: str, documented_kinds: Sequence[str] = ("unsupported",)
) -> AssertionResult:
    """The tool ran and either returned or gave one of its documented errors.

    For a deployment-optional tool (e.g. semantic search when the model stack is
    not installed on the API host), a typed ``unsupported`` failure is the tool's
    *correct*, documented behaviour — reachable, not broken. Any other error, or
    never being called, fails.

    ``documented_kinds`` widens that allowance where a tool documents more than
    one correct failure. It is per-task and explicit on purpose: a fixture whose
    precondition is unmet (semantic search over sentences nobody embedded) must
    name the kind it expects, so a *different* typed failure still fails the
    assertion instead of being absorbed by a blanket tolerance.

    Args:
        calls: The task transcript.
        tool: The tool whose reachability is asserted.
        documented_kinds: Error kinds that count as correct behaviour.

    Returns:
        The assertion result, naming the kind when the tool failed as documented.
    """
    called = [c for c in calls if c.tool == tool]
    if not called:
        return AssertionResult(f"{tool}_reachable", False, "tool was never called")
    allowed = tuple(documented_kinds)
    for call in called:
        if not call.ok and call.error_kind not in allowed:
            return AssertionResult(f"{tool}_reachable", False, f"raised [{call.error_kind}]")
    documented = next((c.error_kind for c in called if not c.ok), None)
    return AssertionResult(
        f"{tool}_reachable", True, f"documented {documented}" if documented else "ok"
    )


# ---------------------------------------------------------------------------
# report assembly + baseline comparison (pure)
# ---------------------------------------------------------------------------


def task_report(
    task_id: str,
    grounded_in: str,
    calls: Calls,
    results: tuple[AssertionResult, ...],
    budget: TaskBudget,
) -> dict[str, Any]:
    """A deterministic, machine-readable record for one task run (no wall-clock)."""
    rep = budget_report(calls, budget)
    return {
        "task_id": task_id,
        "grounded_in": grounded_in,
        "passed": all(r.passed for r in results),
        "calls": rep.calls,
        "chars": rep.chars,
        "est_tokens": rep.est_tokens,
        "within_budget": rep.within,
        "budget": {"max_calls": budget.max_calls, "max_chars": budget.max_chars},
        "assertions": [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in results],
    }


def aggregate(task_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll the per-task reports up into pass/fail counts and metric totals."""
    return {
        "tasks": len(task_reports),
        "passed": sum(1 for r in task_reports if r["passed"]),
        "failed": sum(1 for r in task_reports if not r["passed"]),
        "total_calls": sum(r["calls"] for r in task_reports),
        "total_chars": sum(r["chars"] for r in task_reports),
        "total_est_tokens": sum(r["est_tokens"] for r in task_reports),
    }


def build_report(mode: str, task_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the full report body (deterministic; the runner stamps wall-clock)."""
    return {"mode": mode, "tasks": list(task_reports), "aggregate": aggregate(task_reports)}


def render_table(report: dict[str, Any]) -> str:
    """A human-readable per-task table plus an aggregate line."""
    header = f"{'task':<40} {'result':<6} {'calls':>5} {'chars':>7} {'~tok':>6} {'budget':>7}"
    lines = [header, "-" * len(header)]
    for t in report["tasks"]:
        lines.append(
            f"{t['task_id']:<40} {'PASS' if t['passed'] else 'FAIL':<6} "
            f"{t['calls']:>5} {t['chars']:>7} {t['est_tokens']:>6} "
            f"{'ok' if t['within_budget'] else 'OVER':>7}"
        )
    agg = report["aggregate"]
    lines.append("-" * len(header))
    lines.append(
        f"{'AGGREGATE (' + report.get('mode', '?') + ')':<40} "
        f"{str(agg['passed']) + '/' + str(agg['tasks']):<6} "
        f"{agg['total_calls']:>5} {agg['total_chars']:>7} {agg['total_est_tokens']:>6}"
    )
    return "\n".join(lines)


def compare(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    char_tolerance: float,
    call_tolerance: int,
) -> dict[str, Any]:
    """Compare a run against a stored baseline; flag regressions beyond tolerance.

    A regression is any of: a task that passed in the baseline now failing, a
    call-count increase beyond *call_tolerance*, or a payload-size increase beyond
    *char_tolerance* (a fraction). A task absent from the baseline is new, not a
    regression. This is what makes ``eval_mcp.py --baseline`` a gate.
    """
    reasons: list[str] = []
    base_by_id = {t["task_id"]: t for t in baseline["tasks"]}
    for t in current["tasks"]:
        base = base_by_id.get(t["task_id"])
        if base is None:
            continue
        if base["passed"] and not t["passed"]:
            reasons.append(f"{t['task_id']}: was PASS, now FAIL")
        if t["calls"] > base["calls"] + call_tolerance:
            reasons.append(
                f"{t['task_id']}: calls {base['calls']} → {t['calls']} (> +{call_tolerance})"
            )
        char_ceiling = base["chars"] * (1 + char_tolerance)
        if t["chars"] > char_ceiling:
            reasons.append(
                f"{t['task_id']}: chars {base['chars']} → {t['chars']} (> +{char_tolerance:.0%})"
            )
    return {"regressed": bool(reasons), "reasons": reasons}
