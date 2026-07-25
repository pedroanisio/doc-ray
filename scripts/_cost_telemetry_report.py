"""Rendering for the pipeline cost telemetry report (Markdown and JSON).

Split from ``cost_telemetry.py`` so the measurement/model half stays free of
presentation concerns and both stay under the D-015 net-LOC cap.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

_FRONTMATTER = """---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "scripts/cost_telemetry.py (measured from the live jobs table)"
  date: "{date}"
---
"""


def _fmt(value: float, places: int = 2) -> str:
    """Format a float, rendering nan as an em dash."""
    return "—" if math.isnan(value) else f"{value:,.{places}f}"


def _stage_rows(models: list[Any]) -> list[str]:
    """Render one Markdown table row per fitted stage model."""
    rows = []
    for m in models:
        rows.append(
            f"| `{m.kind}` | {m.lane} | {m.n} | {_fmt(m.fixed_s, 1)} | "
            f"{_fmt(m.per_sentence_s, 5)} | {_fmt(m.correlation, 2)} | "
            f"{_fmt(m.p50_s, 1)} | {_fmt(m.p90_s, 1)} | {_fmt(m.observed_hours, 2)} |"
        )
    return rows


def _projection_rows(projections: list[Any], costs: list[Any]) -> list[str]:
    """Render one Markdown table row per projected corpus size."""
    rows = []
    for projection, cost in zip(projections, costs, strict=True):
        total = f"{cost.total:,.2f}" if cost.lines else "—"
        flag = "" if cost.complete else " *"
        rows.append(
            f"| {projection.documents:,} | {projection.sentences:,.0f} | "
            f"{_fmt(projection.lane_hours.get('gpu', 0.0))} | "
            f"{_fmt(projection.lane_hours.get('cpu', 0.0))} | "
            f"{_fmt(projection.db_gb, 1)} | {_fmt(projection.object_gb, 1)} | "
            f"{total}{flag} |"
        )
    return rows


def _rate_rows(rates: Any, sources: dict[str, str]) -> list[str]:
    """Render the unit-price audit trail."""
    rows = []
    for name, value in asdict(rates).items():
        shown = "**unset**" if value is None else f"{value:,.4f}"
        rows.append(f"| `{name}` | {shown} | {sources.get(name, '—')} |")
    return rows


def render_markdown(payload: dict[str, Any]) -> str:
    """Render the full report as Markdown."""
    profile = payload["profile"]
    models = payload["models"]
    costs = payload["costs"]
    unpriced = sorted({line for cost in costs for line in cost.unpriced})
    lines = [
        _FRONTMATTER.format(date=datetime.now(UTC).strftime("%Y-%m-%d")),
        "# doc-ray — Pipeline Cost Telemetry",
        "",
        "## Disclaimer",
        "",
        "This work is subject to the methodological caveats and commitments described in "
        "[@DISCLAIMER.md](../DISCLAIMER.md).",
        "> No statement or premise not backed by a real logical definition or verifiable "
        "reference should be taken for granted.",
        "",
        "## Measured corpus",
        "",
        f"- Documents: **{profile.documents:,}**",
        f"- Sentences: **{profile.sentences:,}** "
        f"({profile.sentences_per_document:,.0f} per document)",
        f"- Source bytes: **{profile.source_bytes / 1024**3:,.2f} GB** "
        f"({profile.source_bytes / max(profile.documents, 1) / 1024**2:,.1f} MB per document)",
        f"- Database size: **{profile.db_bytes / 1024**3:,.2f} GB** "
        f"({profile.db_bytes / max(profile.documents, 1) / 1024**2:,.1f} MB per document, "
        f"{profile.db_bytes / max(profile.sentences, 1) / 1024:,.1f} KB per sentence)",
        f"- Measurement window: last {payload['window_hours']:g} h of completed job runs",
        "",
        "## Per-stage cost model",
        "",
        "Fitted as `seconds = fixed + per_sentence x sentences` over successful runs. "
        "A low correlation means the fixed term dominates and the stage is "
        "size-insensitive.",
        "",
        "| stage | lane | runs | fixed (s) | s/sentence | r | p50 (s) | p90 (s) | observed h |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        *_stage_rows(models),
        "",
        "## Projected resource draw",
        "",
        f"Storage priced over **{payload['months']:g} month(s)**; compute is a one-off per "
        "document ingested.",
        "",
        "| documents | sentences | GPU h | CPU h | DB GB | object GB | cost |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        *_projection_rows(payload["projections"], costs),
        "",
    ]
    if unpriced:
        named = ", ".join(f"`{line}`" for line in unpriced)
        lines += [
            f"\\* Incomplete total — no rate supplied for {named}. Unpriced lines are "
            "excluded from the total rather than counted as zero; set them in "
            "`config/cost_rates.yaml` for a complete figure.",
            "",
        ]
    lines += [
        "## Unit prices used",
        "",
        "| rate | value (USD) | source |",
        "|---|---:|---|",
        *_rate_rows(payload["rates"], payload["rate_sources"]),
        "",
        "## What drives the cost",
        "",
        "- **Sentences, not bytes.** Stage duration tracks sentence count; source file "
        "size is a weak proxy (a large scanned PDF yields little text, a dense "
        "monograph yields a lot).",
        "- **The GPU lane is the throughput ceiling.** Each worker pool holds one job "
        "at a time, so GPU hours — dominated by `annotate` — set wall-clock time to "
        "process a batch. CPU-lane stages overlap with it and are effectively free "
        "in wall-clock terms.",
        "- **Storage is recurring, compute is not.** Compute is paid once per document; "
        "the database and object store are paid every month the corpus exists, and the "
        "database is the larger of the two by an order of magnitude.",
        "",
    ]
    return "\n".join(lines)


def render_json(payload: dict[str, Any]) -> str:
    """Render the full report as JSON."""
    profile = payload["profile"]
    return json.dumps(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "window_hours": payload["window_hours"],
            "months": payload["months"],
            "corpus": {
                **asdict(profile),
                "sentences_per_document": profile.sentences_per_document,
            },
            "stages": [asdict(m) for m in payload["models"]],
            "projections": [asdict(p) for p in payload["projections"]],
            "costs": [
                {"lines": c.lines, "unpriced": c.unpriced, "total": c.total, "complete": c.complete}
                for c in payload["costs"]
            ],
            "rates": asdict(payload["rates"]),
            "rate_sources": payload["rate_sources"],
        },
        indent=2,
        default=str,
    )
