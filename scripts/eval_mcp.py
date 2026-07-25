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
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT / "src" / "doc_ray" / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

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


def _compare_baseline(report, path, char_tolerance, call_tolerance) -> int:
    baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    result = scoring.compare(
        report, baseline, char_tolerance=char_tolerance, call_tolerance=call_tolerance
    )
    if result["regressed"]:
        print(f"\nREGRESSION vs {path}:", file=sys.stderr)
        for reason in result["reasons"]:
            print(f"  - {reason}", file=sys.stderr)
        return _EXIT_REGRESSION
    print(
        f"\nno regression vs {path} (char tol {char_tolerance:.0%}, call tol {call_tolerance}).",
        file=sys.stderr,
    )
    return _EXIT_OK


def run_scripted_mode(args) -> int:
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
    report["wall_clock_seconds"] = round(time.perf_counter() - started, 3)
    print(scoring.render_table(report))

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
            report, args.baseline, args.char_tolerance, args.call_tolerance
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
        "--verbose", action="store_true", help="log every tool call: tool, args, bytes, duration"
    )
    args = parser.parse_args(argv)

    if args.mode == "agentic":
        print(f"error: {mcp_eval.unsupported_agentic()}", file=sys.stderr)
        return _EXIT_UNSUPPORTED
    return run_scripted_mode(args)


if __name__ == "__main__":
    raise SystemExit(main())
