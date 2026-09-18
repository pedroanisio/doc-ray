#!/usr/bin/env python3
"""Derive pipeline cost telemetry from the jobs table and project operating cost.

Every number here is measured, not assumed: the ``jobs`` table records
``started_at``/``finished_at`` per stage per document, ``sentences`` records the
work unit each stage actually consumes, and ``pg_database_size`` records what the
result costs to keep. This reads those, fits a per-stage cost model, and projects
it onto a corpus of any size.

The unit of work is a **sentence**, not a megabyte. On the reference corpus the
annotate stage correlates with sentence count at r=0.91 and with source bytes at
r=0.32 — a 300 MB scanned PDF with little text is cheap, a dense 12 MB monograph
is not. Stage models are therefore ``seconds = fixed + per_sentence x sentences``.

Compute is split into two lanes because the deployment runs two worker pools: the
GPU pool serves :data:`~doc_ray.domain.jobs.LONG_RUNNING_JOB_KINDS`, and the fast
pool serves everything else (its ``WORKER_EXCLUDE_KINDS`` is exactly that set).
Lane hours are wall-clock hours a single-slot worker is occupied, so they map
directly onto host-hours billed.

Money is never invented. Rates come from ``config/cost_rates.yaml`` (or flags);
any rate left unset makes its line item *unpriced* — reported by name and
excluded from the total, rather than silently counted as zero.

Usage::

    set -a; source .env; set +a
    python scripts/cost_telemetry.py                         # measure + report
    python scripts/cost_telemetry.py --documents 100000      # project to scale
    python scripts/cost_telemetry.py --months 12 --json      # machine-readable
    python scripts/cost_telemetry.py --out "$DOCRAY_WORK_DIR/findings/cost-model.md"

Exit codes: 0 = report produced; 2 = the corpus holds no completed stage runs.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from _cost_telemetry_report import render_json, render_markdown

from doc_ray.adapters import persistence as db
from doc_ray.config import Config
from doc_ray.domain.jobs import LONG_RUNNING_JOB_KINDS

#: Worker pool a stage runs on. The GPU pool holds one job at a time, so its
#: hours are the ones that gate throughput.
LANE_GPU = "gpu"
LANE_CPU = "cpu"

BYTES_PER_GB = 1024**3

DEFAULT_RATES_PATH = "config/cost_rates.yaml"

#: Cost lines, in report order, mapped to the projection field they price.
_LINES = ("gpu_compute", "cpu_compute", "db_storage", "object_storage")


@dataclass(frozen=True)
class StageModel:
    """Measured cost of one job kind as a function of document sentence count.

    Attributes:
        kind: The job kind.
        lane: ``LANE_GPU`` or ``LANE_CPU`` — which worker pool pays for it.
        n: Number of completed runs the fit is based on.
        fixed_s: Per-run cost independent of document size, in seconds.
        per_sentence_s: Marginal seconds per sentence.
        correlation: Pearson r between duration and sentence count; low values
            mean the linear model explains little and the fixed term dominates.
        p50_s: Median observed duration.
        p90_s: 90th-percentile observed duration.
        observed_hours: Total wall-clock hours this kind consumed in the window.
    """

    kind: str
    lane: str
    n: int
    fixed_s: float
    per_sentence_s: float
    correlation: float
    p50_s: float
    p90_s: float
    observed_hours: float


@dataclass(frozen=True)
class CorpusProfile:
    """The measured shape of the corpus the models were fitted against."""

    documents: int
    sentences: int
    source_bytes: int
    db_bytes: int

    @property
    def sentences_per_document(self) -> float:
        """Mean sentences per document, or 0.0 for an empty corpus."""
        return self.sentences / self.documents if self.documents else 0.0


@dataclass(frozen=True)
class Projection:
    """Resource draw for a corpus of *documents*, before any pricing."""

    documents: int
    sentences: float
    lane_hours: dict[str, float]
    db_gb: float
    object_gb: float


@dataclass(frozen=True)
class Rates:
    """Operator-supplied unit prices. ``None`` means "not supplied", never zero."""

    gpu_host_hourly: float | None = None
    cpu_host_hourly: float | None = None
    db_gb_month: float | None = None
    object_gb_month: float | None = None


@dataclass(frozen=True)
class Cost:
    """Priced projection: per-line amounts plus the lines that had no rate."""

    lines: dict[str, float] = field(default_factory=dict)
    unpriced: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        """Sum of the priced lines only."""
        return sum(self.lines.values())

    @property
    def complete(self) -> bool:
        """True when every line item had a rate."""
        return not self.unpriced


def lane_for_kind(kind: str) -> str:
    """Return the worker lane *kind* runs on."""
    return LANE_GPU if kind in LONG_RUNNING_JOB_KINDS else LANE_CPU


def fit_linear(samples: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares fit of ``y = fixed + slope * x`` over ``(x, y)`` *samples*.

    Falls back to a flat mean (slope 0) when x does not vary or fewer than two
    samples exist — a slope through a single x-value is unidentifiable, and
    reporting one would dress noise up as a size relationship.

    Args:
        samples: ``(driver, seconds)`` pairs.

    Returns:
        ``(fixed, slope, correlation)``; correlation is ``nan`` when undefined.
    """
    n = len(samples)
    if n == 0:
        return 0.0, 0.0, math.nan
    mean_y = sum(y for _, y in samples) / n
    if n < 2:
        return mean_y, 0.0, math.nan
    mean_x = sum(x for x, _ in samples) / n
    sxx = sum((x - mean_x) ** 2 for x, _ in samples)
    syy = sum((y - mean_y) ** 2 for _, y in samples)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in samples)
    if sxx == 0:
        return mean_y, 0.0, math.nan
    slope = sxy / sxx
    corr = sxy / math.sqrt(sxx * syy) if syy > 0 else math.nan
    return mean_y - slope * mean_x, slope, corr


def stage_seconds(model: StageModel, sentences: float) -> float:
    """Predicted seconds for one document of *sentences* length, floored at zero."""
    return max(0.0, model.fixed_s + model.per_sentence_s * sentences)


def project(models: list[StageModel], profile: CorpusProfile, documents: int) -> Projection:
    """Scale the measured models and storage footprint to *documents* documents.

    Storage scales per document (the measured bytes-per-document ratio, which
    already includes indexes and the annotation graph); compute scales per
    sentence through each stage.

    Args:
        models: Fitted per-stage models.
        profile: The corpus the models were measured against.
        documents: Corpus size to project onto.

    Returns:
        The projected resource draw.
    """
    per_doc_sentences = profile.sentences_per_document
    lane_hours = {LANE_GPU: 0.0, LANE_CPU: 0.0}
    for model in models:
        lane_hours[model.lane] += stage_seconds(model, per_doc_sentences) * documents / 3600.0
    db_per_doc = profile.db_bytes / profile.documents if profile.documents else 0.0
    object_per_doc = profile.source_bytes / profile.documents if profile.documents else 0.0
    return Projection(
        documents=documents,
        sentences=per_doc_sentences * documents,
        lane_hours=lane_hours,
        db_gb=db_per_doc * documents / BYTES_PER_GB,
        object_gb=object_per_doc * documents / BYTES_PER_GB,
    )


def estimate_cost(projection: Projection, rates: Rates, months: float) -> Cost:
    """Price *projection*, carrying storage for *months* and compute once.

    Compute is a one-off per document processed; storage recurs for as long as
    the corpus is kept, so only the storage lines scale with *months*.

    Args:
        projection: Resource draw to price.
        rates: Unit prices; ``None`` entries leave their line unpriced.
        months: Retention horizon applied to the storage lines.

    Returns:
        A :class:`Cost` holding the priced lines and the unpriced line names.
    """
    quantities: dict[str, tuple[float, float | None]] = {
        "gpu_compute": (projection.lane_hours.get(LANE_GPU, 0.0), rates.gpu_host_hourly),
        "cpu_compute": (projection.lane_hours.get(LANE_CPU, 0.0), rates.cpu_host_hourly),
        "db_storage": (projection.db_gb * months, rates.db_gb_month),
        "object_storage": (projection.object_gb * months, rates.object_gb_month),
    }
    lines: dict[str, float] = {}
    unpriced: list[str] = []
    for name in _LINES:
        quantity, rate = quantities[name]
        if rate is None:
            unpriced.append(name)
        else:
            lines[name] = quantity * rate
    return Cost(lines=lines, unpriced=unpriced)


def measure_stages(conn: Any, since_hours: float) -> list[StageModel]:
    """Fit a :class:`StageModel` per job kind from completed runs in the window.

    Only successful runs count: a job that OOM-failed after 4 seconds measures
    the failure, not the work. Retried attempts are included at their final
    successful duration, which is what a rerun would cost.
    """
    rows = conn.execute(
        """
        SELECT j.kind,
               extract(epoch FROM j.finished_at - j.started_at) AS secs,
               (SELECT count(*) FROM sentences s WHERE s.document_id = j.document_id) AS sentences
          FROM jobs j
         WHERE j.status = 'succeeded'
           AND j.document_id IS NOT NULL
           AND j.started_at >= now() - make_interval(hours => %s)
        """,
        (int(since_hours),),
    ).fetchall()
    by_kind: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        by_kind.setdefault(str(row["kind"]), []).append(
            (float(row["sentences"]), float(row["secs"]))
        )
    models: list[StageModel] = []
    for kind, samples in by_kind.items():
        fixed, per_sentence, corr = fit_linear(samples)
        durations = sorted(secs for _, secs in samples)
        models.append(
            StageModel(
                kind=kind,
                lane=lane_for_kind(kind),
                n=len(samples),
                fixed_s=fixed,
                per_sentence_s=per_sentence,
                correlation=corr,
                p50_s=_percentile(durations, 0.5),
                p90_s=_percentile(durations, 0.9),
                observed_hours=sum(durations) / 3600.0,
            )
        )
    return sorted(models, key=lambda m: m.observed_hours, reverse=True)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an already-sorted list (0.0 when empty)."""
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def measure_corpus(conn: Any) -> CorpusProfile:
    """Read the corpus shape the stage models were fitted against."""
    row = conn.execute(
        "SELECT count(*) AS documents, coalesce(sum(byte_size), 0) AS source_bytes FROM documents"
    ).fetchone()
    sentences = conn.execute("SELECT count(*) AS n FROM sentences").fetchone()["n"]
    db_bytes = conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]
    return CorpusProfile(
        documents=int(row["documents"]),
        sentences=int(sentences),
        source_bytes=int(row["source_bytes"]),
        db_bytes=int(db_bytes),
    )


def load_rates(path: Path, overrides: dict[str, float | None]) -> tuple[Rates, dict[str, str]]:
    """Load unit prices from *path*, applying CLI *overrides* on top.

    The file is optional and every key is optional: a rate that is absent, null,
    or unparseable stays ``None`` so its line reports as unpriced.

    Returns:
        ``(rates, sources)`` where sources maps a rate name to its provenance
        string, for the report's audit trail.
    """
    values: dict[str, float | None] = dict.fromkeys(
        ("gpu_host_hourly", "cpu_host_hourly", "db_gb_month", "object_gb_month")
    )
    sources: dict[str, str] = {}
    if path.exists():
        parsed = _parse_rates_file(path.read_text(encoding="utf-8"))
        for name in values:
            entry = parsed.get(name, {})
            if entry.get("value") is not None:
                values[name] = entry["value"]
                sources[name] = str(entry.get("source") or "unattributed")
    for name, override in overrides.items():
        if override is not None:
            values[name] = override
            sources[name] = "command-line flag"
    return Rates(**values), sources  # type: ignore[arg-type]


def _parse_rates_file(text: str) -> dict[str, dict[str, Any]]:
    """Parse the rates file's ``name: {value, source}`` blocks without a YAML dep.

    The format is deliberately trivial — two indent levels, scalar values — so
    this tool adds no dependency to read four numbers.
    """
    parsed: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        key, value = key.strip(), value.strip()
        if indent <= 2 and not value:
            current = key
            parsed.setdefault(current, {})
        elif current is not None and indent > 2:
            parsed[current][key] = _scalar(value)
    return parsed


def _scalar(raw: str) -> Any:
    """Coerce a scalar field to float, or None for null/empty, else the string."""
    text = raw.strip().strip("'\"")
    if text in ("", "null", "~"):
        return None
    try:
        return float(text)
    except ValueError:
        return text


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="cost_telemetry",
        description="Derive pipeline cost telemetry from measured job runs.",
    )
    parser.add_argument(
        "--since-hours", type=float, default=24.0, help="Measurement window (default: 24)."
    )
    parser.add_argument(
        "--documents",
        type=int,
        action="append",
        help="Corpus size(s) to project onto; repeatable (default: 1k/10k/100k).",
    )
    parser.add_argument(
        "--months", type=float, default=1.0, help="Storage retention horizon (default: 1)."
    )
    parser.add_argument("--rates", default=DEFAULT_RATES_PATH, help="Path to the rates file.")
    parser.add_argument("--gpu-hourly", type=float, default=None, help="Override GPU host rate.")
    parser.add_argument("--cpu-hourly", type=float, default=None, help="Override CPU host rate.")
    parser.add_argument("--db-gb-month", type=float, default=None, help="Override DB storage rate.")
    parser.add_argument(
        "--object-gb-month", type=float, default=None, help="Override object storage rate."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown.")
    parser.add_argument("--out", default=None, help="Write the report to this path.")
    return parser


def run(args: argparse.Namespace) -> int:
    """Measure, project, price, and render; return the process exit code."""
    rates, sources = load_rates(
        Path(args.rates),
        {
            "gpu_host_hourly": args.gpu_hourly,
            "cpu_host_hourly": args.cpu_hourly,
            "db_gb_month": args.db_gb_month,
            "object_gb_month": args.object_gb_month,
        },
    )
    with db.connect(Config.from_env()) as conn:
        models = measure_stages(conn, args.since_hours)
        profile = measure_corpus(conn)
    if not models:
        print(
            f"error: no successful stage runs in the last {args.since_hours:g}h to measure",
            file=sys.stderr,
        )
        return 2
    scales = args.documents or [1_000, 10_000, 100_000]
    projections = [project(models, profile, n) for n in sorted(scales)]
    costs = [estimate_cost(p, rates, args.months) for p in projections]
    payload = {
        "window_hours": args.since_hours,
        "months": args.months,
        "profile": profile,
        "models": models,
        "projections": projections,
        "costs": costs,
        "rates": rates,
        "rate_sources": sources,
    }
    text = render_json(payload) if args.json else render_markdown(payload)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and produce the report."""
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
