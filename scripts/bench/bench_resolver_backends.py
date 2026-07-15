"""Benchmark candidate resolver models against the Stage-3 gold set.

Answers one operational question: *which local Ollama model can replace the
Anthropic backend for the frontier entity resolver?* It drives the **real**
resolver code path — the backend-agnostic seam in
:mod:`doc_ray.adapters.resolver_shared` that both production adapters call — so
the numbers reflect exactly what the worker would commit, with no re-implemented
decision logic to drift.

For every (model, case) pair it records:

* **raw** model output (untrusted), then the PALS-validated final ``Resolution``
  via :func:`resolver_shared.to_resolution` — identical to the adapters;
* whether the raw verdict was schema-valid, and whether PALS had to **coerce** an
  out-of-set / malformed id to ABSTAIN (how hard the model fights the schema);
* wall-clock latency (model warm-up call excluded).

Scores per model: verdict accuracy, select-id accuracy, abstain recall,
over-abstain rate (review-queue flooding), hard-error rate (a confident WRONG
link — the one outcome PALS exists to prevent), coercion count, and latency
p50/p95. Claude is scored the same way when ``ANTHROPIC_API_KEY`` is set, as the
reference to beat.

Usage::

    # start ollama + pull the models first (see scripts/bench/README.md)
    python scripts/bench/bench_resolver_backends.py \\
        --models qwen2.5:32b-instruct llama3.3:70b-instruct-q4_K_M mistral-small \\
        --host http://localhost:11434 \\
        --out scripts/bench/out

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of error.
Absence of output verification is a design defect, not a runtime bug. This harness
therefore scores each model on how it behaves *after* verification, never on raw
output alone.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Make the package importable when run from a plain checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from doc_ray.adapters.resolver_shared import (  # noqa: E402
    SYSTEM_PROMPT,
    CandidateResolutionVerifier,
    build_schema,
    render_prompt,
    to_resolution,
)
from doc_ray.ports.resolver import Candidate, Resolution, ResolutionKind  # noqa: E402

GOLD_DEFAULT = Path(__file__).with_name("resolver_gold.json")
WARMUP_MENTION = "__warmup__"


@dataclass
class Case:
    """One gold resolution case with its human-assigned expected verdict."""

    id: str
    mention: str
    sentence: str
    candidates: list[Candidate]
    expected_kind: ResolutionKind
    expected_id: int | None
    bait_id: int | None

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> Case:
        """Parse one gold-file case object."""
        cands = [
            Candidate(
                canonical_id=int(c["canonical_id"]),
                preferred=str(c["preferred"]),
                entity_type=str(c["entity_type"]),
                description=c.get("description"),
            )
            for c in obj.get("candidates", [])
        ]
        exp = obj["expected"]
        return cls(
            id=str(obj["id"]),
            mention=str(obj["mention"]),
            sentence=str(obj.get("sentence", "")),
            candidates=cands,
            expected_kind=ResolutionKind(exp["kind"]),
            expected_id=exp.get("canonical_id"),
            bait_id=exp.get("bait_id"),
        )


@dataclass
class CaseResult:
    """Outcome of one model on one case."""

    case_id: str
    latency_s: float
    raw_valid: bool
    coerced: bool
    final: Resolution
    correct: bool
    hard_error: bool
    over_abstain: bool


@dataclass
class ModelReport:
    """Aggregated scores for one model over the whole gold set."""

    model: str
    results: list[CaseResult] = field(default_factory=list)

    def _n(self) -> int:
        return len(self.results)

    def summary(self) -> dict[str, Any]:
        """Reduce per-case results to the operational metrics that decide adoption."""
        n = self._n() or 1
        lat = sorted(r.latency_s for r in self.results)
        correct = sum(r.correct for r in self.results)
        hard = sum(r.hard_error for r in self.results)
        over = sum(r.over_abstain for r in self.results)
        coerced = sum(r.coerced for r in self.results)
        invalid = sum(not r.raw_valid for r in self.results)
        return {
            "model": self.model,
            "cases": self._n(),
            "verdict_accuracy": round(correct / n, 3),
            "hard_error_rate": round(hard / n, 3),
            "over_abstain_rate": round(over / n, 3),
            "pals_coercions": coerced,
            "schema_invalid": invalid,
            "latency_p50_s": round(_pct(lat, 50), 2),
            "latency_p95_s": round(_pct(lat, 95), 2),
        }


def _pct(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile of an already-sorted list (0.0 if empty)."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, round((pct / 100.0) * len(sorted_vals) + 0.5) - 1))
    return sorted_vals[k]


def _score(case: Case, final: Resolution) -> tuple[bool, bool, bool]:
    """Classify a final verdict against the gold label.

    Returns:
        (correct, hard_error, over_abstain). ``hard_error`` is the outcome PALS
        exists to prevent — a confident wrong commit (selecting the bait id, or
        selecting/declaring where the truth is abstain). ``over_abstain`` is a
        soft miss: abstaining where a real answer existed (floods human review).
    """
    exp = case.expected_kind
    got = final.kind
    if exp is ResolutionKind.SELECT:
        correct = got is ResolutionKind.SELECT and final.canonical_id == case.expected_id
        hard = got is ResolutionKind.SELECT and final.canonical_id != case.expected_id
        over = got is ResolutionKind.ABSTAIN
        return correct, hard, over
    if exp is ResolutionKind.NEW:
        correct = got is ResolutionKind.NEW
        # Selecting the bait (a wrong existing id) is a hard error; abstain is soft.
        hard = got is ResolutionKind.SELECT
        over = got is ResolutionKind.ABSTAIN
        return correct, hard, over
    # expected ABSTAIN: any commit is a hard error.
    correct = got is ResolutionKind.ABSTAIN
    hard = got is not ResolutionKind.ABSTAIN
    return correct, hard, False


def _ollama_raw(
    client: httpx.Client, host: str, model: str, case: Case
) -> tuple[dict[str, Any] | None, float]:
    """Send exactly what OllamaResolver sends; return (raw_dict_or_None, latency_s).

    Mirrors :meth:`OllamaResolver.resolve`'s single ``/api/chat`` call byte-for-byte
    (same system prompt, rendered prompt, and ``format`` schema) so the measured
    behaviour is the production behaviour. Only the ~6-line transport is inlined;
    all decision logic stays in ``resolver_shared``.
    """
    t0 = time.monotonic()
    resp = client.post(
        f"{host.rstrip('/')}/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": build_schema(case.candidates),
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": render_prompt(case.mention, case.sentence, case.candidates),
                },
            ],
        },
    )
    latency = time.monotonic() - t0
    resp.raise_for_status()
    content = (resp.json().get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        return None, latency
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return None, latency
    return (parsed if isinstance(parsed, dict) else None), latency


def _pull_model(host: str, model: str, timeout: float) -> None:
    """Pull a model into the Ollama server before scoring (``ollama pull`` over HTTP).

    Blocks until the pull completes (``stream=False``). Transport/HTTP errors
    propagate so a mistyped tag or an offline server fails loud, not silently.
    """
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{host.rstrip('/')}/api/pull", json={"model": model, "stream": False})
        resp.raise_for_status()
        status = resp.json().get("status") if isinstance(resp.json(), dict) else None
        if status != "success":
            raise RuntimeError(f"pull of {model!r} did not report success: {status!r}")


def _run_ollama(host: str, model: str, cases: list[Case], timeout: float) -> ModelReport:
    """Score one Ollama model over every case (warm-up call excluded from timing)."""
    report = ModelReport(model=f"ollama/{model}")
    with httpx.Client(timeout=timeout) as client:
        _warmup(client, host, model)
        for case in cases:
            raw, latency = _ollama_raw(client, host, model, case)
            report.results.append(_grade(case, raw, latency))
    return report


def _warmup(client: httpx.Client, host: str, model: str) -> None:
    """Force the model into VRAM so the first real case is not penalised."""
    warm = Case(WARMUP_MENTION, WARMUP_MENTION, "", [], ResolutionKind.ABSTAIN, None, None)
    # A warm-up failure is non-fatal; it surfaces again on the first real case.
    with contextlib.suppress(httpx.HTTPError):
        _ollama_raw(client, host, model, warm)


def _grade(case: Case, raw: dict[str, Any] | None, latency: float) -> CaseResult:
    """Apply PALS verification + scoring to one raw model output."""
    verifier = CandidateResolutionVerifier(case.candidates)
    raw_valid = raw is not None and verifier.verify(raw).ok
    final = to_resolution(raw, case.candidates) if raw is not None else _abstain()
    # Coercion: the raw verdict claimed a SELECT that PALS demoted to ABSTAIN.
    coerced = bool(
        raw is not None
        and raw.get("verdict") == ResolutionKind.SELECT.value
        and final.kind is not ResolutionKind.SELECT
    )
    correct, hard, over = _score(case, final)
    return CaseResult(case.id, latency, raw_valid, coerced, final, correct, hard, over)


def _abstain() -> Resolution:
    """Fail-safe verdict mirroring the adapters."""
    return Resolution(kind=ResolutionKind.ABSTAIN, confidence=0.0)


def _run_claude(cases: list[Case]) -> ModelReport | None:
    """Score the Anthropic backend as the reference (skipped without an API key/SDK)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        from doc_ray.adapters.resolver_claude import ClaudeResolver
    except ImportError:
        return None
    resolver = ClaudeResolver()
    report = ModelReport(model=resolver.name)
    for case in cases:
        t0 = time.monotonic()
        final = resolver.resolve(case.mention, case.sentence, case.candidates)
        latency = time.monotonic() - t0
        # Claude's raw is enforced by a strict tool; treat the returned verdict as valid.
        correct, hard, over = _score(case, final)
        report.results.append(CaseResult(case.id, latency, True, False, final, correct, hard, over))
    return report


def _load_cases(path: Path) -> list[Case]:
    """Load and parse the gold file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Case.from_json(c) for c in data["cases"]]


def _render_markdown(reports: list[ModelReport]) -> str:
    """Render the leaderboard as a disclaimer-fronted Markdown report."""
    header = (
        "---\n"
        "disclaimer:\n"
        "  notice: >-\n"
        "    No information within this document should be taken for granted.\n"
        "    Any statement or premise not backed by a real logical definition\n"
        "    or verifiable reference may be invalid, erroneous, or a hallucination.\n"
        '  generated_by: "scripts/bench/bench_resolver_backends.py"\n'
        '  date: "run-date-not-embedded (see JSON sidecar)"\n'
        "---\n\n"
        "# Resolver backend benchmark\n\n"
        "Lower `hard_error_rate` is the primary criterion (a confident wrong link is the "
        "outcome PALS exists to prevent); then `verdict_accuracy`, then `over_abstain_rate` "
        "(review-queue load), then latency.\n\n"
        "| model | cases | verdict_acc | hard_err | over_abstain | coercions | invalid "
        "| p50 s | p95 s |\n"
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|\n"
    )
    rows = []
    for s in (r.summary() for r in reports):
        rows.append(
            f"| {s['model']} | {s['cases']} | {s['verdict_accuracy']} | {s['hard_error_rate']} "
            f"| {s['over_abstain_rate']} | {s['pals_coercions']} | {s['schema_invalid']} "
            f"| {s['latency_p50_s']} | {s['latency_p95_s']} |"
        )
    return header + "\n".join(rows) + "\n"


def _rank(reports: list[ModelReport]) -> list[ModelReport]:
    """Order models by the adoption decision: hard-error, accuracy, over-abstain, latency."""

    def key(r: ModelReport) -> tuple[float, float, float, float]:
        s = r.summary()
        return (
            s["hard_error_rate"],
            -s["verdict_accuracy"],
            s["over_abstain_rate"],
            s["latency_p50_s"],
        )

    return sorted(reports, key=key)


def _render_inspection(reports: list[ModelReport], cases: list[Case]) -> str:
    """Human-readable ranking + every per-case miss, so a run can be eyeballed."""
    by_id = {c.id: c for c in cases}
    ranked = _rank(reports)
    lines = ["", "=== INSPECTION ===", ""]
    if ranked:
        win = ranked[0].summary()
        lines.append(
            f"Recommended: {win['model']} "
            f"(hard_err={win['hard_error_rate']}, acc={win['verdict_accuracy']}, "
            f"over_abstain={win['over_abstain_rate']}, p50={win['latency_p50_s']}s)"
        )
        lines.append("")
    for report in ranked:
        s = report.summary()
        lines.append(
            f"{s['model']}: acc={s['verdict_accuracy']} hard_err={s['hard_error_rate']} "
            f"over_abstain={s['over_abstain_rate']} coercions={s['pals_coercions']} "
            f"invalid={s['schema_invalid']} p50={s['latency_p50_s']}s p95={s['latency_p95_s']}s"
        )
        for cr in report.results:
            if cr.correct:
                continue
            case = by_id.get(cr.case_id)
            want = case.expected_kind.value if case else "?"
            want_id = f"/{case.expected_id}" if case and case.expected_id is not None else ""
            tag = "HARD" if cr.hard_error else ("over-abstain" if cr.over_abstain else "miss")
            got = f"{cr.final.kind.value}/{cr.final.canonical_id}"
            note = "" if cr.raw_valid else " [schema-invalid]"
            note += " [coerced]" if cr.coerced else ""
            lines.append(f"    [{tag}] {cr.case_id}: want {want}{want_id}, got {got}{note}")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI surface."""
    p = argparse.ArgumentParser(description="Benchmark resolver models against the gold set.")
    p.add_argument("--models", nargs="+", required=True, help="Ollama model tags to score.")
    p.add_argument("--host", default=os.environ.get("DOCRAY_OLLAMA_HOST", "http://localhost:11434"))
    p.add_argument("--gold", type=Path, default=GOLD_DEFAULT)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--pull", action="store_true", help="Pull each model before scoring.")
    p.add_argument("--pull-timeout", type=float, default=3600.0, help="Per-model pull timeout (s).")
    p.add_argument("--out", type=Path, default=Path("scripts/bench/out"))
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    """Run every model over the gold set and write the leaderboard + raw JSON."""
    args = _parse_args(argv)
    cases = _load_cases(args.gold)
    reports: list[ModelReport] = []

    claude = _run_claude(cases)
    if claude is not None:
        reports.append(claude)
    else:
        print("[info] Anthropic reference skipped (no ANTHROPIC_API_KEY / SDK).", file=sys.stderr)

    for model in args.models:
        if args.pull:
            print(f"[info] pulling {model} ...", file=sys.stderr)
            _pull_model(args.host, model, args.pull_timeout)
        print(f"[info] scoring ollama/{model} over {len(cases)} cases ...", file=sys.stderr)
        reports.append(_run_ollama(args.host, model, cases, args.timeout))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "leaderboard.md").write_text(_render_markdown(reports), encoding="utf-8")
    sidecar = {
        r.model: {
            "summary": r.summary(),
            "cases": [
                {
                    "case_id": cr.case_id,
                    "latency_s": round(cr.latency_s, 3),
                    "raw_valid": cr.raw_valid,
                    "coerced": cr.coerced,
                    "kind": cr.final.kind.value,
                    "canonical_id": cr.final.canonical_id,
                    "correct": cr.correct,
                    "hard_error": cr.hard_error,
                    "over_abstain": cr.over_abstain,
                }
                for cr in r.results
            ],
        }
        for r in reports
    }
    (args.out / "results.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    inspection = _render_inspection(reports, cases)
    (args.out / "inspection.txt").write_text(inspection, encoding="utf-8")
    print(_render_markdown(reports))
    print(inspection)
    print(
        f"[info] wrote {args.out / 'leaderboard.md'}, {args.out / 'results.json'}, "
        f"{args.out / 'inspection.txt'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
