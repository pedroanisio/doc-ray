"""Drift guard for the MCP evaluation task set (issue #12).

The load-bearing check: **every registered MCP read tool is exercised by at least
one evaluation task**. As the tool surface grows, a new read tool that no task
covers fails this build — which is what keeps the harness honest instead of
quietly measuring an ever-shrinking fraction of the surface. The remaining checks
keep the task set itself well-formed (FR-2's six grounded tasks present, no
malformed or duplicate tasks). All static — no database required.
"""

from __future__ import annotations

from doc_ray.application.mcp_eval import validate_task
from doc_ray.application.mcp_eval_tasks import TASKS
from doc_ray.entrypoints.mcp import compose, corpus, semantic, structure

#: The authoritative read surface — exactly what ``server.build_server`` registers.
_REGISTERED_READ_TOOLS = frozenset(
    fn.__name__ for fn in (*corpus.TOOLS, *semantic.TOOLS, *structure.TOOLS, *compose.TOOLS)
)

#: The six grounded tasks FR-2 mandates.
_FR2_GROUNDED = frozenset(
    {
        "cover-document-exhaustively",
        "find-rare-scene",
        "survive-keyword-flood",
        "read-the-ending",
        "entity-roster-not-fooled-by-type-split",
        "cursor-not-interchangeable",
    }
)


def _tools_touched() -> frozenset[str]:
    touched: set[str] = set()
    for task in TASKS:
        touched |= task.tools_touched
    return frozenset(touched)


def test_every_registered_read_tool_is_exercised_by_at_least_one_task():
    missing = sorted(_REGISTERED_READ_TOOLS - _tools_touched())
    assert not missing, (
        f"registered read tool(s) exercised by no eval task: {missing}. Add a task that "
        "calls each (a coverage task is fine), or the harness stops measuring the surface."
    )


def test_no_task_references_a_tool_that_is_not_a_registered_read_tool():
    phantom = sorted(_tools_touched() - _REGISTERED_READ_TOOLS)
    assert not phantom, f"task(s) reference non-registered tool(s): {phantom}"


def test_the_six_grounded_fr2_tasks_are_present():
    grounded = {t.id for t in TASKS if t.kind == "grounded"}
    assert grounded >= _FR2_GROUNDED, (
        f"missing FR-2 grounded tasks: {sorted(_FR2_GROUNDED - grounded)}"
    )


def test_every_task_is_well_formed_with_a_unique_id():
    for task in TASKS:
        validate_task(task)  # raises on empty steps/assertions or unknown kind
    ids = [t.id for t in TASKS]
    assert len(ids) == len(set(ids)), "duplicate task ids in the registry"


def test_the_guard_can_actually_fail_when_a_tool_is_dropped():
    # Self-test so the coverage guard never passes vacuously: drop a real tool from
    # the touched set and confirm it is then reported as missing.
    reduced = _tools_touched() - {"search_sentences"}
    assert "search_sentences" in (_REGISTERED_READ_TOOLS - reduced)
