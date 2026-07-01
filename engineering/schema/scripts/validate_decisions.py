#!/usr/bin/env python3
"""engineering_decisions.yml validator — CLI entrypoint.

Usage:
    validate_decisions.py FILE [FILE ...]
    validate_decisions.py --rules
    validate_decisions.py --help

Examples:
    # Validate one file (text output).
    scripts/validate_decisions.py engineering_decisions.yml

    # Validate against a custom project category list.
    scripts/validate_decisions.py engineering_decisions.yml \\
        --config statics/ttl/engineering_decisions_config.ttl

    # Validate the canonical template + a project fixture.
    scripts/validate_decisions.py \\
        statics/templates/engineering_decisions_template.yml \\
        statics/fixtures/engineering_decisions.quality.yml

    # JSON output for CI / editor integration.
    scripts/validate_decisions.py engineering_decisions.yml --format json

    # Treat warnings as errors (CI gate).
    scripts/validate_decisions.py engineering_decisions.yml --strict

    # List every rule the validator can fire.
    scripts/validate_decisions.py --rules

Exit codes:
    0 — no errors (warnings allowed unless --strict)
    1 — validation errors found
    2 — usage error (bad CLI flags)
    3 — file could not be read or parsed
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError as e:
    print(f"error: PyYAML is required ({e})", file=sys.stderr)
    sys.exit(2)

# Make the validator library importable whether or not scripts/ is on PYTHONPATH.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import validator as V  # noqa: E402


_REPO_ROOT = _HERE.parent
DEFAULT_CONFIG_TTL = _REPO_ROOT / "statics" / "ttl" / "engineering_decisions_config.ttl"

_EXIT_OK = 0
_EXIT_VALIDATION_FAILED = 1
_EXIT_USAGE = 2
_EXIT_IO = 3


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate_decisions.py",
        description=(
            "Validate an engineering_decisions.yml file against the v2 "
            "schema (statics/ttl/engineering_decisions_*.ttl). Catches "
            "structural and lifecycle violations directly from the YAML, "
            "without needing to project to RDF first."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes: 0 = no errors; 1 = errors; 2 = usage; 3 = I/O.\n"
            "See `--rules` for the full rule catalogue."
        ),
    )
    p.add_argument(
        "files", nargs="*", type=Path,
        help="One or more YAML files to validate.",
    )
    p.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text).",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as errors (affects exit code).",
    )
    p.add_argument(
        "--no-warnings", action="store_true",
        help="Suppress warnings in text output (still counted in summary).",
    )
    p.add_argument(
        "--quiet", "-q", action="store_true",
        help="Only print the summary line (text format).",
    )
    p.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_TTL,
        help=(
            "Path to engineering_decisions_config.ttl. Used to read the "
            "live category enum. Default: the repo-local config TTL."
        ),
    )
    p.add_argument(
        "--allow-context", action="append", default=None,
        choices=sorted(V.VALID_CONTEXT),
        help=(
            "Override cfg:allowedCheckContexts. May be passed multiple "
            "times. Default: workspace, ci (the project's pinned policy)."
        ),
    )
    p.add_argument(
        "--no-strict-side-effects", action="store_true",
        help=(
            "Relax cfg:strictSideEffectsOnShell=true. By default, shell "
            "and python-call checks MUST declare side_effects:."
        ),
    )
    p.add_argument(
        "--rules", action="store_true",
        help="List every rule the validator can fire, then exit.",
    )
    return p


def _load_yaml(path: Path) -> tuple[dict | None, str | None]:
    """Return (doc, error). Exactly one is None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"cannot read {path}: {e}"
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return None, f"YAML parse error in {path}: {e}"
    if not isinstance(doc, dict):
        return None, f"top-level of {path} is not a mapping"
    return doc, None


def _resolve_categories(config_path: Path | None) -> frozenset[str]:
    if config_path is None:
        return V.DEFAULT_CATEGORIES
    live = V.extract_categories_from_config_ttl(config_path)
    if live is None:
        print(
            f"warning: could not read live categories from {config_path}; "
            f"falling back to pinned defaults",
            file=sys.stderr,
        )
        return V.DEFAULT_CATEGORIES
    return live


def _print_rules_catalogue() -> None:
    print(f"{'RULE ID':<55} DESCRIPTION")
    print("-" * 100)
    for rule_id, desc in V.list_rules():
        print(f"{rule_id:<55} {desc}")


def _print_text(
    path: Path,
    report: V.Report,
    *,
    no_warnings: bool,
    quiet: bool,
    strict: bool,
) -> None:
    header = f"== {path} =="
    if quiet:
        return  # summary is printed by the caller in --quiet mode
    print(header)
    visible = [
        f for f in report.findings
        if not (no_warnings and f.severity is V.Severity.WARNING)
    ]
    if not visible:
        print("  (no findings)")
        return
    # Group by severity for readability: errors first, then warnings, then infos.
    order = {V.Severity.ERROR: 0, V.Severity.WARNING: 1, V.Severity.INFO: 2}
    for f in sorted(visible, key=lambda x: (order[x.severity], x.rule, x.target)):
        print(f.format())


def _summary_line(path: Path, report: V.Report, *, strict: bool) -> str:
    n_err = len(report.errors)
    n_warn = len(report.warnings)
    n_info = len(report.infos)
    if strict:
        effective_failed = n_err + n_warn
    else:
        effective_failed = n_err
    verdict = "PASS" if effective_failed == 0 else "FAIL"
    return (
        f"{verdict}  {path}  "
        f"errors={n_err} warnings={n_warn} info={n_info}"
        f"{'  (strict)' if strict else ''}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.rules:
        _print_rules_catalogue()
        return _EXIT_OK

    if not args.files:
        parser.print_usage(sys.stderr)
        print("error: at least one FILE is required (or use --rules)",
              file=sys.stderr)
        return _EXIT_USAGE

    categories = _resolve_categories(args.config)
    allowed_ctx = (frozenset(args.allow_context)
                   if args.allow_context
                   else V.DEFAULT_ALLOWED_CHECK_CONTEXTS)
    strict_se = not args.no_strict_side_effects

    json_results: list[dict] = []
    io_failed = False
    any_validation_failed = False

    for path in args.files:
        doc, err = _load_yaml(path)
        if doc is None:
            io_failed = True
            if args.format == "json":
                json_results.append({
                    "file": str(path),
                    "is_valid": False,
                    "io_error": err,
                    "counts": {"error": 1, "warning": 0, "info": 0},
                    "findings": [{
                        "rule": "io/read-or-parse",
                        "severity": "error",
                        "target": "<file>",
                        "message": err,
                    }],
                })
            else:
                print(f"== {path} ==")
                print(f"  [ERROR  ] io/read-or-parse  <file>: {err}")
                print(_summary_line(path, V.Report([V.Finding(
                    "io/read-or-parse", V.Severity.ERROR, "<file>", err,
                )]), strict=args.strict))
            continue

        report = V.validate(
            doc,
            categories=categories,
            allowed_check_contexts=allowed_ctx,
            strict_side_effects_on_shell=strict_se,
        )
        n_err = len(report.errors)
        n_warn = len(report.warnings)
        failed_for_exit = n_err > 0 or (args.strict and n_warn > 0)
        if failed_for_exit:
            any_validation_failed = True

        if args.format == "json":
            entry = report.to_dict() | {"file": str(path)}
            json_results.append(entry)
        else:
            _print_text(
                path, report,
                no_warnings=args.no_warnings,
                quiet=args.quiet,
                strict=args.strict,
            )
            print(_summary_line(path, report, strict=args.strict))
            if path != args.files[-1]:
                print()

    if args.format == "json":
        out = {
            "is_valid": not any_validation_failed and not io_failed,
            "strict": args.strict,
            "files": json_results,
        }
        json.dump(out, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")

    if io_failed and not any_validation_failed:
        return _EXIT_IO
    if any_validation_failed:
        return _EXIT_VALIDATION_FAILED
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
