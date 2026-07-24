#!/usr/bin/env python3
"""Mechanically extract a quantitative inventory of what this codebase offers.

The inventory is derived from implementation evidence, never from prose. Nine
collectors enumerate the capability surfaces — GraphQL fields, MCP tools and
resources, HTTP and webapp routes, background job kinds, ingestible formats,
annotation layers and language models, configuration variables, operator scripts
— and each detected capability is emitted with its category, source location,
evidence signals, and a confidence band computed from those signals.

Most collectors work by *executing* the production registries (building the real
schema, importing the real tool tuples, walking the real route tables), so the
inventory reports what the deployed system actually serves. Where no runtime
registry exists the record is marked ``static-declaration`` and scores lower.
Collector failures are reported as failures and reduce stated coverage; they are
never silently dropped.

Usage::

    python scripts/gen_feature_inventory.py                       # markdown to stdout
    python scripts/gen_feature_inventory.py --format json         # full records as JSON
    python scripts/gen_feature_inventory.py --out docs/FEATURES.md
    python scripts/gen_feature_inventory.py --summary             # counts only
    python scripts/gen_feature_inventory.py --category mcp.tool.read --category job.kind
    python scripts/gen_feature_inventory.py --min-confidence high

Exit codes: 0 = every collector succeeded; 1 = at least one collector failed
(the inventory is still written, with the failure declared inline).
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _feature_inventory_collectors import COLLECTORS  # noqa: E402
from _feature_inventory_model import (  # noqa: E402
    CONFIDENCE_BANDS,
    ROOT,
    SIGNAL_WEIGHTS,
    CollectorResult,
    CorroborationIndex,
    Feature,
    apply_corroboration,
)

CONFIDENCE_ORDER = ("high", "medium", "low")

_DISCLAIMER = """---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "scripts/gen_feature_inventory.py (mechanized extraction)"
  date: "{date}"
---
"""


def _git_commit() -> str:
    """Short HEAD hash, or ``unknown`` outside a git checkout."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def _package_version() -> str:
    """Declared package version, or ``unknown`` when the package will not import."""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        import doc_ray

        return str(doc_ray.__version__)
    except Exception:  # noqa: BLE001 - a version read must never abort the inventory
        return "unknown"


def _latest_migration() -> str:
    """Filename of the highest-numbered SQL migration, or ``none``."""
    migrations = sorted((ROOT / "db" / "migrations").glob("*.sql"))
    return migrations[-1].name if migrations else "none"


def run_collectors() -> list[CollectorResult]:
    """Run every collector, capturing failures instead of propagating them."""
    results: list[CollectorResult] = []
    for collector in COLLECTORS:
        name = collector.__name__.replace("collect_", "")
        try:
            results.append(collector())
        except Exception as exc:  # noqa: BLE001 - one broken surface must not blind the rest
            results.append(
                CollectorResult(name=name, method="failed", error=f"{type(exc).__name__}: {exc}")
            )
    return results


def build_inventory() -> tuple[list[CollectorResult], list[Feature], CorroborationIndex]:
    """Collect every surface and attach test corroboration to the records."""
    results = run_collectors()
    features = [f for r in results for f in r.features]
    index = CorroborationIndex()
    apply_corroboration(features, index)
    return results, features, index


def filter_features(
    features: list[Feature], categories: list[str], min_confidence: str
) -> list[Feature]:
    """Apply the CLI's category and confidence filters."""
    ceiling = CONFIDENCE_ORDER.index(min_confidence)
    keep = set(CONFIDENCE_ORDER[: ceiling + 1])
    selected = [f for f in features if f.confidence in keep]
    if categories:
        selected = [f for f in selected if f.category in categories]
    return selected


def summarize(features: list[Feature]) -> dict[str, dict[str, int]]:
    """Per-category counts, split by confidence band."""
    summary: dict[str, dict[str, int]] = {}
    for feature in features:
        row = summary.setdefault(
            feature.category, {"total": 0, "high": 0, "medium": 0, "low": 0, "tested": 0}
        )
        row["total"] += 1
        row[feature.confidence] += 1
        if feature.test_refs:
            row["tested"] += 1
    return dict(sorted(summary.items()))


def _meta(index: CorroborationIndex) -> dict[str, object]:
    """Provenance block describing the extraction run itself."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "package_version": _package_version(),
        "git_commit": _git_commit(),
        "latest_migration": _latest_migration(),
        "python": platform.python_version(),
        "test_files_indexed": index.file_count,
        "signal_weights": SIGNAL_WEIGHTS,
        "confidence_bands": {band: threshold for threshold, band in CONFIDENCE_BANDS},
    }


def render_json(
    results: list[CollectorResult], features: list[Feature], index: CorroborationIndex
) -> str:
    """Full machine-readable inventory."""
    payload = {
        "meta": _meta(index),
        "collectors": [
            {
                "name": r.name,
                "method": r.method,
                "ok": r.ok,
                "error": r.error,
                "features_detected": len(r.features),
            }
            for r in results
        ],
        "summary": summarize(features),
        "totals": {
            "features": len(features),
            **{band: sum(1 for f in features if f.confidence == band) for band in CONFIDENCE_ORDER},
        },
        "features": [f.as_dict() for f in features],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Render a GitHub-flavoured markdown table."""
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    return out


def _escape(text: str, limit: int = 120) -> str:
    """Trim a description to table width."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_markdown(
    results: list[CollectorResult],
    features: list[Feature],
    index: CorroborationIndex,
    detail: bool = True,
) -> str:
    """Human-readable inventory: provenance, integrity, counts, then detail."""
    meta = _meta(index)
    lines = [_DISCLAIMER.format(date=str(meta["generated_at"])[:10]).rstrip(), ""]
    lines += [
        "# doc-ray — Feature Inventory (mechanized extraction)",
        "",
        f"- **Package version**: {meta['package_version']}",
        f"- **Commit**: {meta['git_commit']}",
        f"- **Latest migration**: {meta['latest_migration']}",
        f"- **Extracted**: {meta['generated_at']} (Python {meta['python']})",
        f"- **Test files indexed for corroboration**: {meta['test_files_indexed']}",
        "",
        "Every row below was produced by a collector reading an implementation "
        "artifact — a built GraphQL schema, a registered tool tuple, a live route "
        "table, a dispatch dict, or a checked-in declarative registry. Confidence "
        "is computed from evidence signals, not asserted.",
        "",
        "## Extraction integrity",
        "",
    ]
    lines += _table(
        ["Collector", "Method", "Status", "Detected"],
        [
            [
                r.name,
                r.method,
                "ok" if r.ok else f"FAILED — {_escape(r.error, 80)}",
                str(len(r.features)),
            ]
            for r in results
        ],
    )
    failed = [r for r in results if not r.ok]
    lines += [
        "",
        (
            f"**{len(failed)} of {len(results)} collectors failed — coverage below is partial.**"
            if failed
            else f"All {len(results)} collectors completed; coverage is complete for the "
            "surfaces they cover."
        ),
        "",
        "## Evidence model",
        "",
    ]
    lines += _table(
        ["Signal", "Weight", "Meaning"],
        [
            ["runtime-registry", "2", "Enumerated by executing the production wiring"],
            ["static-declaration", "1", "Parsed from a checked-in declarative source of truth"],
            ["source-located", "1", "Capability resolved to a concrete file (and line)"],
            ["documented", "1", "Docstring or registry description present"],
            ["tested", "1", "Identifier referenced by at least one test file"],
            ["handler-bound", "1", "Declared identifier wired to an executable handler"],
        ],
    )
    bands = ", ".join(f"{band} ≥ {threshold}" for threshold, band in CONFIDENCE_BANDS)
    lines += [
        "",
        f"Confidence bands by total score: {bands}. Records without a "
        "`runtime-registry` signal are capped at `medium`: a declaration proves "
        "intent, execution proves existence.",
        "",
        "## Counts by category",
        "",
    ]
    summary = summarize(features)
    lines += _table(
        ["Category", "Features", "High", "Medium", "Low", "Test-corroborated"],
        [
            [
                category,
                str(row["total"]),
                str(row["high"]),
                str(row["medium"]),
                str(row["low"]),
                f"{row['tested']} ({row['tested'] * 100 // max(row['total'], 1)}%)",
            ]
            for category, row in summary.items()
        ]
        + [
            [
                "**total**",
                f"**{len(features)}**",
                *(
                    f"**{sum(1 for f in features if f.confidence == band)}**"
                    for band in CONFIDENCE_ORDER
                ),
                f"**{sum(1 for f in features if f.test_refs)}**",
            ]
        ],
    )
    if not detail:
        return "\n".join(lines) + "\n"
    lines += ["", "## Detected features", ""]
    for category, row in summary.items():
        lines += [f"### {category} ({row['total']})", ""]
        lines += _table(
            ["Feature", "Description", "Source", "Score", "Confidence", "Tests"],
            [
                [
                    f"`{f.name}`",
                    _escape(f.description or f.notes),
                    f.location or "—",
                    str(f.evidence_score),
                    f.confidence,
                    str(f.test_refs),
                ]
                for f in sorted(features, key=lambda f: f.name)
                if f.category == category
            ],
        )
        lines += [""]
    return "\n".join(lines).rstrip() + "\n"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--out", type=Path, help="Write to this path instead of stdout.")
    parser.add_argument(
        "--summary", action="store_true", help="Counts only; omit per-feature detail."
    )
    parser.add_argument(
        "--category", action="append", default=[], help="Restrict to a category (repeatable)."
    )
    parser.add_argument("--min-confidence", choices=CONFIDENCE_ORDER, default="low")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run every collector and emit the inventory."""
    args = parse_args(argv)
    results, features, index = build_inventory()
    selected = filter_features(features, args.category, args.min_confidence)
    if args.format == "json":
        text = render_json(results, selected, index)
    else:
        text = render_markdown(results, selected, index, detail=not args.summary)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out} ({len(selected)} features)", file=sys.stderr)
    else:
        print(text)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
