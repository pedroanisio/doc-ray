#!/usr/bin/env python
"""Run the MCP evaluation harness against the Docker test stack (issue #12).

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of error.
This harness exists to *measure* the agent-facing tool surface empirically instead
of asserting it — the direct application of that law at the programme level.

Usage (from the repo root, inside/against the test stack so DATABASE_URL points at
a disposable test database):

    python scripts/eval_mcp.py --mode scripted
    python scripts/eval_mcp.py --mode scripted --json docs/eval/mcp-baseline.json
    python scripts/eval_mcp.py --mode scripted --baseline docs/eval/mcp-baseline.json

``scripted`` (default) executes a fixed call sequence per task — deterministic and
free. ``agentic`` fails with a typed ``unsupported`` error (not implemented in this
build). ``--baseline`` compares against a stored run and exits non-zero on a
regression beyond tolerance; a task failure also exits non-zero.

``--namespace-experiment`` is a database-free issue-#17 experiment. It rewrites a
copy of the advertised names with explicit family prefixes and measures discovery
cost, but refuses to claim improved model selection because only the unavailable
``agentic`` mode could measure that hypothesis.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT / "src" / "doc_ray" / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

with warnings.catch_warnings():
    # API package import constructs its module-level app and warns about the
    # development object-store URL. The evaluator never uses that object store.
    warnings.filterwarnings(
        "ignore",
        message=r"S3_ENDPOINT uses plaintext http://.*",
        category=RuntimeWarning,
    )
    from doc_ray.application import mcp_eval  # noqa: E402  (after sys.path bootstrap)
    from doc_ray.application.mcp_eval_fixtures import cleanup_world, seed_world  # noqa: E402
    from doc_ray.application.mcp_eval_tasks import TASKS  # noqa: E402
    from doc_ray.domain import mcp_eval_scoring as scoring  # noqa: E402

# Exit codes: 0 ok · 1 baseline regression · 2 task failure · 3 mode unsupported.
_EXIT_OK, _EXIT_REGRESSION, _EXIT_TASK_FAILURE, _EXIT_UNSUPPORTED = 0, 1, 2, 3


def _connect():
    """A guarded sync connection (refuses a live/UAT corpus), via the test helper."""
    import _itdb

    return _itdb.connect()


def _verbose_observer(task_id, call):
    status = "ok " if call.ok else f"ERR[{call.error_kind}]"
    args = {k: v for k, v in call.args.items() if v is not None}
    print(
        f"  [{task_id:<38}] {call.tool:<26} {status} "
        f"{call.response_chars:>6}c {call.duration_s * 1000:>5.0f}ms  {args}",
        file=sys.stderr,
    )


async def _measure_surface() -> dict[str, dict[str, int]]:
    """Measure both live FastMCP discovery surfaces without hard-coded counts."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"S3_ENDPOINT uses plaintext http://.*",
            category=RuntimeWarning,
        )
        from doc_ray.entrypoints.mcp.server import build_server

    surface: dict[str, dict[str, int]] = {}
    for key, enable_write in (("read_only", False), ("write_enabled", True)):
        server = build_server(enable_write=enable_write)
        tools = await server.list_tools()
        tools_json = json.dumps([tool.model_dump(exclude_none=True) for tool in tools])
        surface[key] = {
            "tool_count": len(tools),
            "tools_list_chars": len(tools_json),
            "tools_list_est_tokens": len(tools_json) // 4,
            "instructions_chars": len(server.instructions or ""),
            "resources_count": len(await server.list_resources()),
            "resource_templates_count": len(await server.list_resource_templates()),
            "prompts_count": len(await server.list_prompts()),
        }
    return surface


def _namespace_candidate_renames(*, enable_write: bool) -> dict[str, str]:
    """Return the issue-#17 family-prefix candidate without changing live tools."""
    from doc_ray.entrypoints.mcp import (
        compose,
        corpus,
        mention_confidence,
        mutations,
        mutations_surface,
        semantic,
        structure,
    )

    families: list[tuple[str, Sequence[Any]]] = [
        ("corpus", corpus.TOOLS),
        ("semantic", semantic.TOOLS),
        ("structure", structure.TOOLS),
        ("confidence", mention_confidence.TOOLS),
        ("compose", compose.TOOLS),
    ]
    if enable_write:
        families.append(("write", (*mutations.TOOLS, *mutations_surface.TOOLS)))
    renames = {}
    for family, tools in families:
        prefix = f"{family}_"
        for tool in tools:
            name = tool.__name__
            renames[name] = name if name.startswith(prefix) else f"{prefix}{name}"
    if len(renames) != sum(len(tools) for _, tools in families):
        raise RuntimeError("namespace candidate contains duplicate live tool names")
    return renames


async def _measure_namespace_experiment() -> dict[str, Any]:
    """Measure issue #17's candidate and make the behavioral limitation explicit."""
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"S3_ENDPOINT uses plaintext http://.*",
            category=RuntimeWarning,
        )
        from doc_ray.entrypoints.mcp.server import build_server

    surface: dict[str, dict[str, int]] = {}
    all_renames: dict[str, str] = {}
    for key, enable_write in (("read_only", False), ("write_enabled", True)):
        tools = await build_server(enable_write=enable_write).list_tools()
        renames = _namespace_candidate_renames(enable_write=enable_write)
        current = [tool.model_dump(exclude_none=True) for tool in tools]
        candidate = [dict(item, name=renames[item["name"]]) for item in current]
        current_chars = len(json.dumps(current))
        candidate_chars = len(json.dumps(candidate))
        surface[key] = {
            "current_tool_count": len(current),
            "candidate_tool_count": len(candidate),
            "current_tools_list_chars": current_chars,
            "candidate_tools_list_chars": candidate_chars,
            "delta_chars": candidate_chars - current_chars,
        }
        all_renames.update(renames)

    return {
        "schema_version": 1,
        "issue": 17,
        "hypothesis": "family prefixes improve model tool selection",
        "candidate": {
            "style": "<family>_<existing_name>",
            "renames": dict(sorted(all_renames.items())),
        },
        "surface": surface,
        "behavioral_measurement": {
            "supported": False,
            "required_mode": "agentic",
            "available_mode": "scripted",
            "reason": "scripted tasks bind tool callables and cannot measure model tool selection",
        },
        "status": "inconclusive",
        "decision": "do_not_land",
    }


def _render_surface(surface: dict[str, dict[str, int]]) -> str:
    """Render stable, greppable discovery metrics for both registry modes."""
    lines = ["\nadvertised MCP surface"]
    for key in ("read_only", "write_enabled"):
        row = surface[key]
        lines.append(
            f"  {key:<13} tools={row['tool_count']} "
            f"tools_list_chars={row['tools_list_chars']} "
            f"tools_list_est_tokens={row['tools_list_est_tokens']} "
            f"instructions_chars={row['instructions_chars']} "
            f"resources={row['resources_count']} "
            f"templates={row['resource_templates_count']} prompts={row['prompts_count']}"
        )
    return "\n".join(lines)


def _surface_regressions(
    report: dict[str, Any], baseline: dict[str, Any], tolerance: float
) -> list[str]:
    """Return advertised-surface growth beyond *tolerance* for comparable modes."""
    reasons: list[str] = []
    current_surface = report.get("surface")
    baseline_surface = baseline.get("surface")
    if not isinstance(current_surface, dict) or not isinstance(baseline_surface, dict):
        return reasons
    for key in ("read_only", "write_enabled"):
        current = current_surface.get(key)
        base = baseline_surface.get(key)
        if not isinstance(current, dict) or not isinstance(base, dict):
            continue
        current_chars = current.get("tools_list_chars")
        base_chars = base.get("tools_list_chars")
        if not isinstance(current_chars, int) or not isinstance(base_chars, int):
            continue
        if current_chars > base_chars * (1 + tolerance):
            reasons.append(
                f"surface.{key}: tools_list_chars {base_chars} → {current_chars} "
                f"(> +{tolerance:.0%})"
            )
    return reasons


def _compare_baseline(report, path, char_tolerance, call_tolerance, surface_tolerance) -> int:
    baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    result = scoring.compare(
        report, baseline, char_tolerance=char_tolerance, call_tolerance=call_tolerance
    )
    result["reasons"].extend(_surface_regressions(report, baseline, surface_tolerance))
    result["regressed"] = bool(result["reasons"])
    if result["regressed"]:
        print(f"\nREGRESSION vs {path}:", file=sys.stderr)
        for reason in result["reasons"]:
            print(f"  - {reason}", file=sys.stderr)
        return _EXIT_REGRESSION
    print(
        f"\nno regression vs {path} (char tol {char_tolerance:.0%}, "
        f"call tol {call_tolerance}, surface tol {surface_tolerance:.0%}).",
        file=sys.stderr,
    )
    return _EXIT_OK


def run_scripted_mode(args) -> int:
    surface = asyncio.run(_measure_surface())
    conn = _connect()
    started = time.perf_counter()
    world = seed_world(conn, mcp_eval.HARNESS_PRINCIPAL_ID)
    try:
        observer = _verbose_observer if args.verbose else None
        task_reports = asyncio.run(mcp_eval.run_scripted(TASKS, world, observer=observer))
    finally:
        cleanup_world(conn, world)
        conn.close()

    report = scoring.build_report("scripted", task_reports)
    report["surface"] = surface
    report["wall_clock_seconds"] = round(time.perf_counter() - started, 3)
    print(scoring.render_table(report))
    print(_render_surface(surface))

    if args.json:
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
        Path(args.json).write_text(text, encoding="utf-8")
        print(f"\nwrote machine-readable report to {args.json}", file=sys.stderr)

    exit_code = _EXIT_OK
    failed = [t["task_id"] for t in report["tasks"] if not t["passed"]]
    if failed:
        exit_code = _EXIT_TASK_FAILURE
        print(f"\nFAILED tasks: {failed}", file=sys.stderr)
    if args.baseline:
        regression = _compare_baseline(
            report,
            args.baseline,
            args.char_tolerance,
            args.call_tolerance,
            args.surface_tolerance,
        )
        exit_code = max(exit_code, regression)
    return exit_code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="MCP tool-surface evaluation harness (issue #12).")
    parser.add_argument("--mode", choices=["scripted", "agentic"], default="scripted")
    parser.add_argument("--json", metavar="PATH", help="write the machine-readable report here")
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        help="compare against a stored baseline; non-zero on regression",
    )
    parser.add_argument(
        "--char-tolerance", type=float, default=0.10, help="allowed payload growth fraction (0.10)"
    )
    parser.add_argument(
        "--call-tolerance", type=int, default=0, help="allowed extra tool calls (0)"
    )
    parser.add_argument(
        "--surface-tolerance",
        type=float,
        default=0.05,
        help="allowed tools/list payload growth fraction (0.05)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="log every tool call: tool, args, bytes, duration"
    )
    parser.add_argument(
        "--namespace-experiment",
        metavar="PATH",
        help="write the database-free issue-#17 namespace experiment and exit",
    )
    args = parser.parse_args(argv)

    if args.namespace_experiment:
        report = asyncio.run(_measure_namespace_experiment())
        Path(args.namespace_experiment).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"wrote namespace experiment to {args.namespace_experiment}: "
            f"{report['status']} ({report['decision']})",
            file=sys.stderr,
        )
        return _EXIT_OK
    if args.mode == "agentic":
        print(f"error: {mcp_eval.unsupported_agentic()}", file=sys.stderr)
        return _EXIT_UNSUPPORTED
    return run_scripted_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
