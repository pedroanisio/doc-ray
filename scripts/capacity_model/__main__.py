"""CLI: run the capacity model and emit JSON + Markdown reports.

Usage (from the repo root)::

    python scripts/run_capacity_model.py                     # default run
    python -m capacity_model --hardware scaled               # scaled host as primary
    python -m capacity_model --config my_overrides.json      # override assumptions
    python -m capacity_model --list-assumptions              # show every input
    python -m capacity_model --no-sensitivity                # skip the ~50 re-runs

Outputs land in ``out/capacity_model/`` (gitignored) unless ``--out-dir`` says
otherwise.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

from capacity_model.analysis import SensitivityRow, capacity_margins, sensitivity_analysis
from capacity_model.assumptions import load_override_file
from capacity_model.model import ModelResult, run_model
from capacity_model.report_json import build_json_report, dump_json
from capacity_model.report_md import build_markdown_report

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Build and evaluate the argument parser."""
    p = argparse.ArgumentParser(
        prog="capacity_model",
        description=(
            "Analytical PostgreSQL capacity model for doc-ray at ~10k users / "
            "1M documents. All inputs are named assumptions; see --list-assumptions."
        ),
    )
    p.add_argument(
        "--hardware",
        choices=("prod", "scaled"),
        default="prod",
        help="primary hardware profile (default: the repo-documented prod cluster)",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="JSON file of {assumption-name: value} overrides",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "out" / "capacity_model",
        help="output directory (default: out/capacity_model, gitignored)",
    )
    p.add_argument(
        "--no-sensitivity",
        action="store_true",
        help="skip the halve/double sensitivity re-runs (faster)",
    )
    p.add_argument(
        "--list-assumptions",
        action="store_true",
        help="print every assumption (name, value, source, citation) and exit",
    )
    p.add_argument("--quiet", action="store_true", help="suppress the stdout summary")
    return p.parse_args(argv)


def _print_assumptions(res: ModelResult) -> None:
    """Dump the assumption ledger to stdout."""
    for a in res.registry.entries.values():
        flag = " [sensitivity]" if a.sensitivity_key else ""
        print(f"{a.name} = {a.value:g} {a.unit} ({a.source.value}{flag})")
        print(f"    {a.citation}")


def _print_summary(res: ModelResult, sensitivity: list[SensitivityRow]) -> None:
    """Short stdout digest of the headline results."""
    m = res.headline_metrics()
    print(f"hardware       : {res.hardware.description}")
    print(
        f"total storage  : {m['storage_tb']:,.2f} TB "
        f"(calibration {res.sizes.calibration_ratio:.2f}× vs measured anchor)"
    )
    exp = res.scenario("expected").dynamics
    print(
        f"expected load  : {exp.total_qps:,.0f} stmts/s, "
        f"{exp.commits_s:,.1f} commits/s, WAL {exp.wal_bytes_s / 1e6:,.1f} MB/s"
    )
    for row in capacity_margins(res):
        mult = row["saturation_multiplier"]
        mult_s = f"{mult:,.2f}" if isinstance(mult, float) else "?"
        print(
            f"margin [{row['scenario']:<9}]: binding={row['binding_resource']:<14} "
            f"util={float(str(row['binding_utilization'])) * 100:6.1f}%  "
            f"saturates at x{mult_s}"
        )
    if sensitivity:
        top = ", ".join(r.name for r in sensitivity[:5])
        print(f"most influential assumptions: {top}")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    overrides = load_override_file(args.config) if args.config else {}
    primary = run_model(overrides, hardware=args.hardware)
    if args.list_assumptions:
        _print_assumptions(primary)
        return 0
    other = "scaled" if args.hardware == "prod" else "prod"
    comparison = run_model(overrides, hardware=other)
    sens: list[SensitivityRow] = []
    if not args.no_sensitivity:
        sens = sensitivity_analysis(primary, args.hardware)
    generated_on = _dt.date.today().isoformat()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "capacity_report.json"
    md_path = args.out_dir / "capacity_report.md"
    json_path.write_text(
        dump_json(build_json_report(primary, comparison, sens, generated_on)),
        encoding="utf-8",
    )
    md_path.write_text(
        build_markdown_report(primary, comparison, sens, generated_on),
        encoding="utf-8",
    )
    if not args.quiet:
        _print_summary(primary, sens)
        print(f"reports        : {md_path} , {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
