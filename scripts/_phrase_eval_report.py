"""Report rendering for the phrase-retrieval evaluation (issue #47, P8).

The report is the deliverable, so its integrity rule is executable rather than
editorial: **every number carries its provenance**. A benchmark figure that
cannot be traced to the command that produced it is indistinguishable from an
invented one, and under PALS's Law the missing check — not the number — is the
defect.

:func:`find_unprovenanced_numbers` is that check, and the suite plants a bare
number to prove it can fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import _phrase_eval_criteria as crit

#: Provenance the report declares for itself: the generator, not whoever ran it.
#: Pass ``--generated-by`` on the harness to append the operator or agent.
GENERATED_BY = "scripts/eval_phrase_retrieval.py"

#: Tokens that contain digits but are identifiers, not measurements: issue and
#: claim references, dates, versions, commit shas, ordered-list markers and
#: table rules. Removed before the "does this line state a number?" test.
_NON_MEASUREMENT = re.compile(
    r"""
      \#\d+                     # issue reference, e.g. #47
    | \b[A-Z]{1,3}-\d+\b        # claim / backlog ids, e.g. D-035, BL-185
    | \bP\d+\b                  # programme blocks, e.g. P8
    | (?i:\bstage[\ -]\d+\b)    # architecture phases, e.g. stage 1 / Stage-2
    | \bFR-\d+\b | \bNFR-\d+\b | \bOD-\d+\b | \bTEST-[A-Z]\d+\b
    | \b\d{4}-\d{2}-\d{2}\b     # ISO dates
    | \bv?\d+\.\d+\.\d+\b       # semantic versions
    | \b[0-9a-f]{7,40}\b        # commit shas
    | ^\s*\d+\.\s               # ordered-list marker
    | ^[\s|:-]+$                # table alignment rule
    """,
    re.VERBOSE | re.MULTILINE,
)

#: An explicit prose citation, e.g. "(source: `eval_phrase_retrieval.py --prod`)".
_PROSE_SOURCE = re.compile(r"\(source:[^)]+\)", re.IGNORECASE)


@dataclass(frozen=True)
class EvalRun:
    """Everything measured in one harness invocation."""

    generated_on: str
    leg: str
    database_label: str
    server_measurements: list[crit.ClassMeasurement]
    client_measurements: list[crit.ClassMeasurement]
    decision: crit.Decision
    #: ``None`` when the catalog could not be read (e.g. a never-analyzed
    #: relation reports reltuples = -1), which must render as "not measured".
    price: crit.StageTwoPrice | None
    catalog_provenance: str
    plans: list[tuple[str, str]] = field(default_factory=list)
    read_only_evidence: str = ""


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    _, _, rest = text.partition("\n---\n")
    return rest


def _strip_code_fences(text: str) -> str:
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out)


def find_unprovenanced_numbers(markdown: str) -> list[str]:
    """Return report lines that state a number without saying where it came from.

    A line is provenanced when it is a row of a table whose header names a
    Source column, or when it carries an inline ``(source: ...)`` citation.
    Frontmatter, fenced transcripts and identifier-shaped tokens are exempt.
    """
    body = _strip_code_fences(_strip_frontmatter(markdown))
    offenders: list[str] = []
    header_has_source = False

    for line in body.splitlines():
        stripped = line.strip()
        is_row = stripped.startswith("|")
        if is_row and not header_has_source:
            # First row of a table block is its header.
            if re.match(r"^[|\s:-]+$", stripped):
                pass  # alignment rule; keep whatever the header decided
            elif "source" in stripped.lower() or "provenance" in stripped.lower():
                header_has_source = True
        if not is_row and stripped:
            header_has_source = False

        residue = _NON_MEASUREMENT.sub("", line)
        if not re.search(r"\d", residue):
            continue
        if is_row and header_has_source:
            continue
        if _PROSE_SOURCE.search(line):
            continue
        offenders.append(line.strip())

    return offenders


def _frontmatter(run: EvalRun, generated_by: str = GENERATED_BY) -> str:
    return (
        "---\n"
        "disclaimer:\n"
        "  notice: >-\n"
        "    No information within this document should be taken for granted.\n"
        "    Any statement or premise not backed by a real logical definition\n"
        "    or verifiable reference may be invalid, erroneous, or a hallucination.\n"
        f'  generated_by: "{generated_by}"\n'
        f'  date: "{run.generated_on}"\n'
        "---\n"
    )


def _matrix(rows: list[crit.ClassMeasurement], surface: str) -> str:
    if not rows:
        return f"_The {surface} leg was not measured in this run._\n"
    by_key = {qc.key: qc for qc in crit.QUERY_CLASSES}
    out = [
        "| Class | Phrase | Candidates | p50 (ms) | p95 (ms) | Runs | Outcome | "
        "Boundary FPs | Source |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    any_timeout = False
    for m in rows:
        qc = by_key.get(m.key)
        phrase = f"`{qc.phrase}`" if qc else "—"
        # A timeout floor and a completed run print the same digits; only the
        # outcome column distinguishes "took this long" from "never finished".
        if m.timed_out:
            any_timeout = True
            outcome = "**timed out**"
            p50, p95 = f"≥ {m.p50_ms:.1f}", f"≥ {m.p95_ms:.1f}"
        else:
            outcome = "capped" if m.capped else "completed"
            p50, p95 = f"{m.p50_ms:.1f}", f"{m.p95_ms:.1f}"
        out.append(
            f"| {m.key} | {phrase} | {m.candidates} | {p50} | {p95} | "
            f"{m.runs} | {outcome} | {m.false_positives} | {surface} |"
        )
    table = "\n".join(out) + "\n"
    if any_timeout:
        table += (
            "\nA **timed out** row states a *floor*, not a duration: the scan was "
            "cancelled at the statement timeout, so its true cost is unknown and at "
            "least the value shown. Its candidate count comes from the warm-up run, "
            "which is the only attempt that completed, so it is also a floor.\n"
        )
    return table


def _escalations(rows: list[crit.ClassMeasurement]) -> str:
    flagged = [m.key for m in rows if crit.needs_escalation(m)]
    if not flagged:
        return (
            "No class sits close enough to the budget line for a larger sample to "
            "change the verdict, so OD-2's escalation was not triggered "
            "(source: `_phrase_eval_criteria.needs_escalation`).\n"
        )
    return (
        f"Classes within the escalation band of the budget line: {', '.join(flagged)} "
        "— OD-2 calls for the larger sample here "
        "(source: `_phrase_eval_criteria.needs_escalation`).\n"
    )


def _pricing(run: EvalRun) -> str:
    price = run.price
    if price is None:
        return (
            "Stage-2 pricing was **not measured** on this leg: at least one input\n"
            "relation has never been analyzed, so `pg_class.reltuples` reports\n"
            "*unknown* rather than a row count. Publishing a derived zero here would\n"
            "be a fabricated number, so the section is empty by design.\n"
        )
    return f"""All stage-2 figures are **estimates**: row widths are nominal and `reltuples`
is itself an estimate, not a count.

| Quantity | Value | Source |
|---|---|---|
| Positional index rows | {price.index_rows} | {run.catalog_provenance} |
| Positional index bytes | {price.index_bytes} | derived, estimate |
| Surface registry rows | {price.registry_rows} | {run.catalog_provenance} |
| Surface registry bytes | {price.registry_bytes} | derived, estimate |

Arithmetic, shown in full so the estimate can be checked rather than trusted:

```
{price.arithmetic}
```
"""


def _access_paths(run: EvalRun) -> str:
    """Which access path each class planned — the finding behind the timings."""
    if not run.plans:
        return "Access paths were **not measured** in this run.\n"

    import eval_phrase_retrieval as harness

    rows = [
        "| Class | Access path | Uses an index? | Source |",
        "|---|---|---|---|",
    ]
    seq_scans = []
    for key, plan in run.plans:
        kind = harness.scan_kind(plan) or "not captured"
        indexed = harness.uses_an_index(plan)
        verdict = {True: "yes", False: "**no index**", None: "unknown"}[indexed]
        if indexed is False:
            seq_scans.append(key)
        rows.append(f"| {key} | {kind} | {verdict} | `EXPLAIN`, this harness |")

    table = "\n".join(rows) + "\n"
    if seq_scans:
        table += (
            f"\nEvery class marked **no index** reads the `sentences` fact table end to "
            f"end: {', '.join(seq_scans)}. That is the cost being measured above, and it "
            "is a property of how candidates are generated — not of the verification "
            "step, which is pure and bounded by the candidate cap.\n"
        )
    return table


def _plans(run: EvalRun) -> str:
    if not run.plans:
        return "Query plans were **not measured** in this run.\n"
    parts = []
    for key, transcript in run.plans:
        parts.append(f"### {key}\n\n```\n{transcript}\n```\n")
    return "\n".join(parts)


def render_report(run: EvalRun, generated_by: str | None = None) -> str:
    """Render the dated evaluation report (written under ``$DOCRAY_WORK_DIR``)."""
    decision = run.decision
    provenance = GENERATED_BY if not generated_by else f"{GENERATED_BY}, run by {generated_by}"
    return f"""{_frontmatter(run, provenance)}
# Phrase-retrieval evaluation — stage 1 measured, stage 2 priced

Issue #47 (Retrieval · P8). This report is the gate record for #48: it measures
the shipped verified-phrase path, prices the positional token engine, and states
a go/no-go against fixed criteria rather than judgement.

**Leg:** {run.leg} · **DB:** {run.database_label} (source: `DATABASE_URL`)

Every figure below is produced by `scripts/eval_phrase_retrieval.py`; re-running
that command regenerates this report. Bare numbers are a defect the generator
itself rejects (`_phrase_eval_report.find_unprovenanced_numbers`).

## Verdict — {decision.verdict}

> {decision.rationale} (source: `_phrase_eval_criteria.decide`, FR-5)

Recorded as claim **{crit.DECISION_CLAIM_ID}** in `engineering_decisions.yml`,
status `proposed` until the operator responds on #48.

## Latency — server-side (GraphQL resolver path)

The budget line is the read-surface target from D-025's rationale
(source: `engineering_decisions.yml`, D-025).

{_matrix(run.server_measurements, "server")}
{_escalations(run.server_measurements)}
## Latency — end to end (MCP over loopback)

OD-1 was resolved as *both*: the budget is user-facing, the plans are
server-side, so the two are reported as separate columns rather than merged.

{_matrix(run.client_measurements, "mcp")}
## Correctness

Zero word-boundary false positives is FR-1's precondition; the `Boundary FPs`
column above carries the measurement per class. Agreement between the P6
occurrence list and the P7 counts is only asserted where neither side truncated
— above the shared candidate cap the two order their candidates differently and
sample different subsets, so equality is not expected there
(source: `eval_phrase_retrieval.py`, correctness leg).

## Access paths

{_access_paths(run)}
## Query plans

{_plans(run)}
## Stage-2 pricing (FR-4)

{_pricing(run)}
## Read-only evidence (NFR-3)

The production leg runs inside a read-only transaction with a defensive
statement timeout, and the harness reads the setting back rather than assuming
the `SET` took effect:

```
{run.read_only_evidence or "not measured"}
```

Every statement is additionally screened before execution; a non-read statement
raises rather than reaching the server (`eval_phrase_retrieval.guard_sql`).

## Reproducing this report

```
python scripts/eval_phrase_retrieval.py                # isolated container leg
python scripts/eval_phrase_retrieval.py --prod         # production, read-only
```
"""
