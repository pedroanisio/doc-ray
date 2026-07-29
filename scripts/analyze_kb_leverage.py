#!/usr/bin/env python3
"""Measure whether an external retrieval KB relieves this host's workload.

The question this answers is narrow and falsifiable: *what share of the corpus
pipeline's measured compute could a hosted retrieval index displace, and how much
of the corpus does that index actually cover?* It replaces the intuition that a
knowledge base "helps on a GPU-free host" with numbers taken from the live
`jobs`, `documents`, `sentences`, and `embedding` tables.

It deliberately does NOT ask an LLM anything and does not read the KB's own
answers, so nothing here needs a PALS verification gate — every field is a count
or a ratio derived from the operational database.

RLS: `documents` and friends carry forced row-level security (migration 106) and
the app role does not hold BYPASSRLS. A plain count therefore returns 0 for an
invisible corpus, which is indistinguishable from an empty one. This script
assumes `docray_batch` and then asserts the corpus is non-empty, so an
unreadable corpus fails closed as `error` instead of silently reporting a clean
verdict.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psycopg

#: Role holding BYPASSRLS, created by migration 106 for batch/read tooling.
BATCH_ROLE = "docray_batch"

#: How each job kind relates to a hosted *retrieval* index. Only `displaceable`
#: work could move to a KB; the rest is annotation, vision, or graph compute that
#: a retrieval endpoint cannot perform at all.
KIND_CLASS: dict[str, str] = {
    "semantic_embed": "displaceable",
    "visual_caption": "already_remote",
    "annotate": "not_displaceable",
    "reannotate": "not_displaceable",
    "visual_embed": "not_displaceable",
    "visual_extract": "not_displaceable",
    "score_mentions": "not_displaceable",
    "canonicalize": "not_displaceable",
    "semantic_enrich": "not_displaceable",
    "ingest": "not_displaceable",
    "seed_suggest": "not_displaceable",
}
DEFAULT_CLASS = "not_displaceable"

COMPUTE_SQL = """
SELECT kind,
       count(*),
       coalesce(sum(extract(epoch FROM (finished_at - started_at))) / 3600.0, 0)
FROM jobs
WHERE status = 'succeeded'
  AND finished_at > now() - make_interval(days => %s)
GROUP BY kind
"""

CORPUS_SQL = """
SELECT count(*) FILTER (WHERE status = 'extracted'),
       count(*) FILTER (WHERE status = 'failed'),
       count(*)
FROM documents
WHERE deleted_at IS NULL
"""


@dataclass(frozen=True)
class KindCost:
    """Measured compute for one job kind over the window."""

    kind: str
    classification: str
    runs: int
    hours: float


@dataclass(frozen=True)
class ComputeProfile:
    """How the pipeline's measured compute splits by displaceability."""

    window_days: int
    total_hours: float
    displaceable_hours: float
    already_remote_hours: float
    not_displaceable_hours: float
    displaceable_share: float
    by_kind: tuple[KindCost, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CoverageProfile:
    """Corpus coverage of the external index, against both denominators."""

    documents_total: int
    documents_extracted: int
    documents_failed: int
    kb_submitted: int | None
    kb_indexed: int | None
    #: indexed / submitted — the figure a KB console reports.
    share_of_submitted: float | None
    #: indexed / extractable — the figure that reflects real corpus coverage.
    share_of_extractable: float | None
    never_submitted: int | None


@dataclass(frozen=True)
class SemanticProfile:
    """What doc-ray's own semantic index currently holds."""

    sentences: int
    embeddings: int
    embedded_share: float


@dataclass(frozen=True)
class LeverageReport:
    """Stable machine-readable verdict on external-KB leverage."""

    status: str
    detail: str
    compute: ComputeProfile | None = None
    coverage: CoverageProfile | None = None
    semantic: SemanticProfile | None = None


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a simple dotenv file without logging any values."""
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").lstrip().partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _ratio(numerator: float, denominator: float) -> float:
    """Return numerator/denominator, or 0.0 when the denominator is zero."""
    return round(numerator / denominator, 4) if denominator else 0.0


def build_compute_profile(rows: list[tuple[Any, ...]], window_days: int) -> ComputeProfile:
    """Split measured job hours into displaceable and non-displaceable buckets."""
    by_kind = tuple(
        sorted(
            (
                KindCost(
                    kind=str(kind),
                    classification=KIND_CLASS.get(str(kind), DEFAULT_CLASS),
                    runs=int(runs),
                    hours=round(float(hours), 2),
                )
                for kind, runs, hours in rows
            ),
            key=lambda k: k.hours,
            reverse=True,
        )
    )
    bucket = {"displaceable": 0.0, "already_remote": 0.0, "not_displaceable": 0.0}
    for entry in by_kind:
        bucket[entry.classification] += entry.hours
    total = sum(bucket.values())
    return ComputeProfile(
        window_days=window_days,
        total_hours=round(total, 2),
        displaceable_hours=round(bucket["displaceable"], 2),
        already_remote_hours=round(bucket["already_remote"], 2),
        not_displaceable_hours=round(bucket["not_displaceable"], 2),
        displaceable_share=_ratio(bucket["displaceable"], total),
        by_kind=by_kind,
    )


def build_coverage_profile(
    extracted: int, failed: int, total: int, kb_submitted: int | None, kb_indexed: int | None
) -> CoverageProfile:
    """Express KB coverage against both the submitted and the extractable set."""
    return CoverageProfile(
        documents_total=total,
        documents_extracted=extracted,
        documents_failed=failed,
        kb_submitted=kb_submitted,
        kb_indexed=kb_indexed,
        share_of_submitted=(
            _ratio(kb_indexed, kb_submitted)
            if kb_indexed is not None and kb_submitted is not None
            else None
        ),
        share_of_extractable=(_ratio(kb_indexed, extracted) if kb_indexed is not None else None),
        never_submitted=(extracted - kb_submitted if kb_submitted is not None else None),
    )


def analyze(
    env: Mapping[str, str],
    *,
    window_days: int = 30,
    kb_submitted: int | None = None,
    kb_indexed: int | None = None,
    material_threshold: float = 0.20,
    connect: Callable[..., Any] = psycopg.connect,
) -> LeverageReport:
    """Measure KB leverage, failing closed when the corpus cannot be read."""
    database_url = (env.get("DATABASE_URL") or "").strip()
    if not database_url:
        return LeverageReport("error", "DATABASE_URL is missing")
    try:
        with connect(database_url, autocommit=True) as conn:
            conn.execute(f"SET ROLE {BATCH_ROLE}")
            corpus = conn.execute(CORPUS_SQL).fetchone()
            extracted, failed, total = (int(corpus[0]), int(corpus[1]), int(corpus[2]))
            if total == 0:
                return LeverageReport(
                    "error",
                    "corpus reads as empty — RLS visibility or wrong database; "
                    "refusing to report leverage against an unreadable corpus",
                )
            compute_rows = list(conn.execute(COMPUTE_SQL, (window_days,)).fetchall())
            sentences = int(conn.execute("SELECT count(*) FROM sentences").fetchone()[0])
            embeddings = int(conn.execute("SELECT count(*) FROM embedding").fetchone()[0])
    except Exception as exc:  # noqa: BLE001 - any driver failure is a measurement failure
        return LeverageReport("error", f"{type(exc).__name__}: {exc}")

    compute = build_compute_profile(compute_rows, window_days)
    coverage = build_coverage_profile(extracted, failed, total, kb_submitted, kb_indexed)
    semantic = SemanticProfile(
        sentences=sentences,
        embeddings=embeddings,
        embedded_share=_ratio(embeddings, sentences),
    )
    material = compute.displaceable_share >= material_threshold
    status = "leverage-material" if material else "leverage-marginal"
    detail = (
        f"{compute.displaceable_hours}h of {compute.total_hours}h "
        f"({compute.displaceable_share:.1%}) of measured compute is retrieval-displaceable; "
        f"threshold {material_threshold:.0%}"
    )
    return LeverageReport(status, detail, compute, coverage, semantic)


def _render(report: LeverageReport) -> str:
    """Render a human-readable summary of the report."""
    lines = [f"kb-leverage: {report.status.upper()} — {report.detail}"]
    if report.compute:
        lines.append("")
        lines.append(f"  Compute, last {report.compute.window_days}d (succeeded jobs)")
        for entry in report.compute.by_kind:
            lines.append(
                f"    {entry.kind:<18} {entry.hours:>7.2f} h  "
                f"n={entry.runs:<5} {entry.classification}"
            )
        lines.append(f"    {'displaceable':<18} {report.compute.displaceable_hours:>7.2f} h")
        lines.append(f"    {'already remote':<18} {report.compute.already_remote_hours:>7.2f} h")
        lines.append(
            f"    {'not displaceable':<18} {report.compute.not_displaceable_hours:>7.2f} h"
        )
    cov = report.coverage
    if cov:
        lines.append("")
        lines.append("  Corpus coverage")
        lines.append(
            f"    documents          {cov.documents_total} "
            f"({cov.documents_extracted} extractable, {cov.documents_failed} failed)"
        )
        if cov.kb_indexed is None:
            lines.append("    kb indexed         (not supplied — pass --kb-indexed/--kb-submitted)")
            return "\n".join(lines) + _semantic_lines(report)
        lines.append(f"    kb indexed         {cov.kb_indexed}")
        if cov.share_of_submitted is not None:
            lines.append(
                f"    share of submitted {cov.share_of_submitted:.1%}  <- what a KB console reports"
            )
        if cov.share_of_extractable is not None:
            lines.append(f"    share of corpus    {cov.share_of_extractable:.1%}  <- real coverage")
        if cov.never_submitted is not None:
            lines.append(f"    never submitted    {cov.never_submitted}")
    return "\n".join(lines) + _semantic_lines(report)


def _semantic_lines(report: LeverageReport) -> str:
    """Render the local semantic-index block, or nothing when unmeasured."""
    sem = report.semantic
    if not sem:
        return ""
    return (
        "\n\n  Local semantic index"
        f"\n    sentences          {sem.sentences}"
        f"\n    embeddings         {sem.embeddings}"
        f"\n    embedded share     {sem.embedded_share:.1%}"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the analysis and emit text or JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--kb-submitted", type=int, default=None)
    parser.add_argument("--kb-indexed", type=int, default=None)
    parser.add_argument("--material-threshold", type=float, default=0.20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.env_file.is_file():
        report = LeverageReport("error", f"env file not found: {args.env_file}")
    else:
        env = {**os.environ, **load_env_file(args.env_file)}
        report = analyze(
            env,
            window_days=args.window_days,
            kb_submitted=args.kb_submitted,
            kb_indexed=args.kb_indexed,
            material_threshold=args.material_threshold,
        )
    print(json.dumps(asdict(report), indent=2, sort_keys=True) if args.json else _render(report))
    return {"leverage-material": 0, "leverage-marginal": 1, "error": 2}[report.status]


if __name__ == "__main__":
    raise SystemExit(main())
