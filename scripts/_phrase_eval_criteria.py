"""Pure decision logic for the phrase-retrieval evaluation (issue #47, P8).

Split out of ``eval_phrase_retrieval.py`` so the part that decides the go/no-go
is a function over numbers rather than a paragraph over impressions: same
inputs, same verdict, re-runnable by anyone reading the report (D-015 keeps both
halves under the net-LOC cap).

Nothing here touches a database, a clock or the network. The harness measures;
this module judges.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The read-surface latency budget, from D-025's rationale
#: (engineering_decisions.yml): four read pages were rebuilt to serve within
#: 300-400 ms. The upper bound is the breach line; the band below it is the
#: margin that drives OD-2 escalation.
BUDGET_MS_LOW = 300.0
BUDGET_MS_HIGH = 400.0

#: OD-2: 20 warm runs by default, escalating to 100 only where the extra
#: precision could change the verdict.
DEFAULT_RUNS = 20
ESCALATED_RUNS = 100
#: "Decision-relevant" = p95 sits within +/-20 % of the budget line.
ESCALATION_BAND = 0.20

#: The validated-ADR claim this evaluation records. Issue #47 asked for the
#: third-next id on the assumption that the two before it were reserved by
#: #36-Phase-2 and #38. Re-verified: #36 Phase 2 shipped (f0660de, 1.53.0)
#: without recording any entry and #38 has not started, so the reservation
#: lapsed and this is simply the next free claim.
DECISION_CLAIM_ID = "D-035"

#: Verdicts (FR-5).
GO = "GO"
NO_GO = "NO_GO"
OPERATOR = "OPERATOR"

#: Bytes per positional index row for the stage-2 estimate:
#: surface_term_uid (16) + annotation_id (16) + sentence_id (16) + ordinal (4)
#: = 52 payload bytes, plus a 24-byte index-tuple header/pointer allowance.
INDEX_ROW_BYTES = 76
#: Registry row: uid (16) + surface text (~24 avg) + tuple overhead (~24).
REGISTRY_ROW_BYTES = 64


@dataclass(frozen=True)
class QueryClass:
    """One measured query class from FR-2."""

    key: str
    kind: str
    phrase: str
    why: str
    tolerated_non_selective: bool = False


#: FR-2's matrix. The phrases are fixed so a re-run measures the same thing
#: (NFR-2); `why` is carried into the report so no row is an unexplained number.
QUERY_CLASSES: tuple[QueryClass, ...] = (
    QueryClass(
        key="rare_phrase",
        kind="rare",
        phrase="Model Context Protocol",
        why="Every word is uncommon: the candidate clause should be highly selective.",
    ),
    QueryClass(
        key="mid_phrase",
        kind="mid",
        phrase="algorithm design",
        why="Ordinary technical bigram; the common case for a research query.",
    ),
    QueryClass(
        key="common_words",
        kind="common_words",
        phrase="not happy",
        why="Both words are frequent, so candidates greatly outnumber occurrences.",
    ),
    QueryClass(
        key="all_short_words",
        kind="all_short_words",
        phrase="of it",
        why="Nothing in the phrase is selective; the cap, not the index, bounds it.",
        tolerated_non_selective=True,
    ),
    QueryClass(
        key="joiner",
        kind="joiner",
        phrase="COVID-19",
        why="The profile splits on the hyphen, so one term becomes an adjacency test.",
    ),
    QueryClass(
        key="confusable",
        kind="confusable",
        phrase="Hick's law",
        why="ASCII and typographic apostrophes must be one phrase, not two.",
    ),
    QueryClass(
        key="single_rare_control",
        kind="control",
        phrase="ngram",
        why="Single-word control: isolates phrase cost from ordinary lookup cost.",
    ),
)


@dataclass(frozen=True)
class ClassMeasurement:
    """What one query class measured on one surface."""

    key: str
    p50_ms: float
    p95_ms: float
    runs: int
    capped: bool
    false_positives: int
    #: True/False when P6 occurrences and P7 counts were comparable; None when
    #: either side truncated, where equality is not expected.
    agreement: bool | None
    #: Candidate sentences the scan actually returned. A latency cannot be read
    #: without it: sub-millisecond over nothing is not sub-millisecond over work.
    candidates: int = 0
    #: The statement hit the defensive timeout. The recorded p50/p95 are then
    #: floors at the timeout, not completions.
    timed_out: bool = False


@dataclass(frozen=True)
class Decision:
    verdict: str
    rationale: str


@dataclass(frozen=True)
class StageTwoPrice:
    """FR-4 pricing, with the arithmetic kept alongside the result."""

    index_rows: int
    registry_rows: int
    index_bytes: int
    registry_bytes: int
    arithmetic: str
    is_estimate: bool = True


def percentile(series: list[float], pct: float) -> float:
    """Nearest-rank percentile — deterministic, no interpolation (NFR-2)."""
    if not series:
        raise ValueError("percentile of an empty series would be a fabricated number")
    ordered = sorted(series)
    rank = max(1, min(len(ordered), int(-(-pct / 100 * len(ordered) // 1))))
    return ordered[rank - 1]


def needs_escalation(measurement: ClassMeasurement) -> bool:
    """OD-2: only classes whose p95 sits near the budget line justify 100 runs."""
    if measurement.runs >= ESCALATED_RUNS:
        return False
    low = BUDGET_MS_HIGH * (1 - ESCALATION_BAND)
    high = BUDGET_MS_HIGH * (1 + ESCALATION_BAND)
    return low <= measurement.p95_ms <= high


def _tolerated_keys() -> set[str]:
    return {qc.key for qc in QUERY_CLASSES if qc.tolerated_non_selective}


def decide(measurements: list[ClassMeasurement], *, lemma_requirement: bool) -> Decision:
    """Apply FR-5 to a measured matrix.

    Precedence, and why it is not simply the spec's sentence order:

    FR-5's GO clause says "any structural budget failure", while its NO-GO
    clause explicitly tolerates the all-short-words class being over budget.
    Read literally the two collide, because that class is the one guaranteed to
    saturate its cap — the literal reading makes the stated exemption
    unreachable. The exemption is the more specific rule, so the structural test
    skips exactly the class the exemption names (recorded as OD-3).
    """
    if not measurements:
        raise ValueError("no measurements: there is nothing to decide on")
    missing = {qc.key for qc in QUERY_CLASSES} - {m.key for m in measurements}
    if missing:
        raise ValueError(f"incomplete matrix, cannot decide: missing {sorted(missing)}")

    tolerated = _tolerated_keys()

    if not any(m.candidates for m in measurements) and not any(m.timed_out for m in measurements):
        return Decision(
            OPERATOR,
            "No candidates matched in any class, so every timing here is a scan "
            "over an empty result set. That is a corpus or visibility problem, "
            "not evidence that stage 1 meets its budget.",
        )

    over_budget = [m for m in measurements if m.p95_ms > BUDGET_MS_HIGH or m.timed_out]
    # A timeout is non-selectivity by definition: the candidate LIMIT bounds
    # rows returned, never the scan needed to find them.
    structural = [m for m in over_budget if (m.capped or m.timed_out) and m.key not in tolerated]

    if structural:
        keys = ", ".join(sorted(m.key for m in structural))
        how = (
            "timed out before completing"
            if any(m.timed_out for m in structural)
            else "was already capped"
        )
        return Decision(
            GO,
            f"Structural budget failure in {keys}: the candidate scan {how} "
            f"against a {BUDGET_MS_HIGH:.0f} ms budget, and the cap bounds rows "
            "returned rather than rows scanned, so no cap tuning can hide it.",
        )
    if lemma_requirement:
        return Decision(
            GO,
            "Operator-stated product requirement for lemma-phrase search, which "
            "stage 1 cannot deliver: it matches surfaces, not lemmas.",
        )

    dirty_boundaries = [m for m in measurements if m.false_positives]
    disagreements = [m for m in measurements if m.agreement is False]
    if dirty_boundaries:
        keys = ", ".join(sorted(m.key for m in dirty_boundaries))
        return Decision(
            OPERATOR,
            f"Correctness suite is not clean: word-boundary false positive in "
            f"{keys}. FR-1 requires zero before any latency verdict is meaningful.",
        )
    if disagreements:
        keys = ", ".join(sorted(m.key for m in disagreements))
        return Decision(
            OPERATOR,
            f"P6 and P7 disagree on {keys} despite sharing one profile — a "
            "released bug that must be filed before this gate is decided.",
        )

    untolerated = [m for m in over_budget if m.key not in tolerated]
    if untolerated:
        keys = ", ".join(sorted(m.key for m in untolerated))
        return Decision(
            OPERATOR,
            f"{keys} exceeds the {BUDGET_MS_HIGH:.0f} ms budget without hitting "
            "the candidate cap, so the cost is not structural non-selectivity. "
            "That is a tuning question the operator should price against P9.",
        )

    return Decision(
        NO_GO,
        f"Stay on stage 1: p95 within the {BUDGET_MS_HIGH:.0f} ms budget for "
        "every class except the tolerated non-selective one, and the "
        "correctness suite is clean.",
    )


def price_stage_two(
    *,
    token_rows: int,
    sentence_rows: int,
    distinct_surfaces: int,
    avg_sentence_chars: float,
) -> StageTwoPrice:
    """FR-4: derive the stage-2 cost from measured catalog numbers.

    One positional row per token occurrence, one registry row per distinct
    surface. Every output is an estimate: row widths are nominal and
    ``reltuples`` is itself an estimate.
    """
    if min(token_rows, sentence_rows, distinct_surfaces) < 0 or avg_sentence_chars < 0:
        raise ValueError("catalog measurements cannot be negative")

    index_bytes = token_rows * INDEX_ROW_BYTES
    registry_bytes = distinct_surfaces * REGISTRY_ROW_BYTES
    arithmetic = (
        f"index rows = token rows = {token_rows} (pg_class.reltuples); "
        f"index bytes = {token_rows} x {INDEX_ROW_BYTES} B/row = {index_bytes} B; "
        f"registry rows = distinct surfaces = {distinct_surfaces} (bounded TABLESAMPLE "
        f"extrapolation); registry bytes = {distinct_surfaces} x {REGISTRY_ROW_BYTES} "
        f"B/row = {registry_bytes} B; corpus shape = {sentence_rows} sentences at "
        f"{avg_sentence_chars:.1f} average chars."
    )
    return StageTwoPrice(
        index_rows=token_rows,
        registry_rows=distinct_surfaces,
        index_bytes=index_bytes,
        registry_bytes=registry_bytes,
        arithmetic=arithmetic,
    )
