"""Real-corpus resolver probe (READ-ONLY) — run candidate Ollama models over the
document residue actually sitting in the managed corpus.

Reports, per model: verdict distribution, structured-output safety
(schema-invalid + PALS coercions), latency at real sentence lengths, and — across
models — the pairwise agreement that decides whether a cheaper model is a safe
swap for a bigger one.

⚠ IMPORTANT — WHAT THIS DOES *NOT* TEST. It reconstructs each mention's Stage-2
input exactly as :func:`resolution.resolve_residue` does (surface + sentence +
candidate set), but writes nothing back. When the canonical registry is empty
(freshly ingested corpus, no Stage-2 seeding), every residue mention carries an
EMPTY candidate set, so the schema admits only ``new_entity`` / ``abstain`` — this
probe then exercises the new-vs-abstain path + schema safety at scale, NOT
candidate disambiguation (that needs a seeded registry). The probe prints the
candidate-set size distribution so you can see which regime you are in.

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of error.
Every verdict here is re-validated by resolver_shared.to_resolution — a model that
tries to SELECT from an empty set is coerced to ABSTAIN and counted, never trusted.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bench_resolver_backends as bench  # type: ignore[import-not-found]  # noqa: E402  (sibling harness)
from _work_dir import work_dir  # type: ignore[import-not-found]  # noqa: E402

from doc_ray.adapters import canonical_store as store  # noqa: E402
from doc_ray.adapters.persistence import connect  # noqa: E402
from doc_ray.adapters.resolver_shared import (  # noqa: E402
    CandidateResolutionVerifier,
    to_resolution,
)
from doc_ray.application.candidate_generation import candidates_for_form  # noqa: E402
from doc_ray.application.resolution import (  # noqa: E402
    AUTO_COMMIT_THRESHOLD,
    NON_REFERENTIAL_TYPES,
)
from doc_ray.config import Config  # noqa: E402
from doc_ray.ports.resolver import ResolutionKind  # noqa: E402


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines into the environment (zsh `set -a; source` is unsafe here)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def sample_cases(conn: Any, limit: int) -> list[tuple[str, bench.Case]]:
    """Reconstruct up to *limit* residue cases, read-only, strided across the corpus.

    Mirrors the residue collection in :func:`canonicalize.autolink_document` and the
    sentence lookup in :func:`resolution.resolve_residue`, but performs no writes.
    """
    residue: list[tuple[str, bench.Case]] = []
    docs = [str(r["id"]) for r in conn.execute("SELECT id FROM documents").fetchall()]
    for doc_id in docs:
        for mention in store.mentions_for_document(conn, doc_id):
            surface = (mention["surface"] or "").strip()
            if not surface or mention["entity_type"] in NON_REFERENTIAL_TYPES:
                continue
            blocking = candidates_for_form(conn, surface)
            if blocking.auto_link_id is not None:
                continue  # Stage-2 would auto-link this; never reaches the resolver
            sentence = (
                store.sentence_for_span(conn, doc_id, mention["char_start"], mention["char_end"])
                or ""
            )
            case = bench.Case(
                id=str(mention["mention_id"]),
                mention=surface,
                sentence=sentence,
                candidates=blocking.candidates,
                expected_kind=ResolutionKind.ABSTAIN,  # unused: real corpus has no gold label
                expected_id=None,
                bait_id=None,
            )
            residue.append((str(mention["mention_id"]), case))
    if len(residue) <= limit:
        return residue
    stride = len(residue) / limit  # even coverage, not just the front matter
    return [residue[int(i * stride)] for i in range(limit)]


def score_model(host: str, model: str, cases: list[bench.Case], timeout: float) -> dict[str, Any]:
    """Run one model over the sampled cases; return per-case verdicts + aggregates."""
    import httpx

    verdicts: list[dict[str, Any]] = []
    latencies: list[float] = []
    with httpx.Client(timeout=timeout) as client:
        bench._warmup(client, host, model)
        for case in cases:
            raw, latency = bench._ollama_raw(client, host, model, case)
            latencies.append(latency)
            valid = raw is not None and CandidateResolutionVerifier(case.candidates).verify(raw).ok
            final = (
                to_resolution(raw, case.candidates)
                if raw is not None
                else bench.Resolution(kind=ResolutionKind.ABSTAIN)
            )
            coerced = bool(
                raw is not None
                and raw.get("verdict") == ResolutionKind.SELECT.value
                and final.kind is not ResolutionKind.SELECT
            )
            verdicts.append(
                {
                    "kind": final.kind.value,
                    "canonical_id": final.canonical_id,
                    "new_preferred": final.new_preferred,
                    "confidence": final.confidence,
                    "valid": valid,
                    "coerced": coerced,
                }
            )
    kinds = Counter(v["kind"] for v in verdicts)
    lat = sorted(latencies)
    # What would actually be written vs sent to review: a select/new only auto-commits
    # at confidence >= AUTO_COMMIT_THRESHOLD; everything else queues for a human.
    auto_commit = sum(
        v["kind"] in {"select", "new_entity"} and v["confidence"] >= AUTO_COMMIT_THRESHOLD
        for v in verdicts
    )
    return {
        "model": f"ollama/{model}",
        "n": len(cases),
        "verdicts": verdicts,
        "kind_counts": dict(kinds),
        "auto_commit": auto_commit,
        "review": len(verdicts) - auto_commit,
        "schema_invalid": sum(not v["valid"] for v in verdicts),
        "coercions": sum(v["coerced"] for v in verdicts),
        "latency_p50_s": round(bench._pct(lat, 50), 2),
        "latency_p95_s": round(bench._pct(lat, 95), 2),
    }


def _agreement(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Pairwise agreement between two models' per-case verdicts."""
    va, vb = a["verdicts"], b["verdicts"]
    same_kind = sum(x["kind"] == y["kind"] for x, y in zip(va, vb, strict=True))
    n = len(va) or 1
    disagreements = [
        (i, x["kind"], y["kind"])
        for i, (x, y) in enumerate(zip(va, vb, strict=True))
        if x["kind"] != y["kind"]
    ]
    return {
        "pair": f"{a['model']} vs {b['model']}",
        "kind_agreement": round(same_kind / n, 3),
        "disagreements": disagreements,
    }


def _report(results: list[dict[str, Any]], cases: list[bench.Case]) -> str:
    """Render the probe summary + a sample of cross-model disagreements to eyeball."""
    sizes = Counter(len(c.candidates) for c in cases)
    lines = [
        "=== REAL-CORPUS RESOLVER PROBE (read-only) ===",
        f"sampled cases: {len(cases)}",
        f"candidate-set size distribution (#candidates -> #cases): {dict(sorted(sizes.items()))}",
        "  (all-zero ⇒ empty registry: new-vs-abstain regime, SELECT untestable)",
        "",
    ]
    for r in results:
        lines.append(
            f"{r['model']}: verdicts={r['kind_counts']} "
            f"auto_commit={r['auto_commit']} review={r['review']} "
            f"schema_invalid={r['schema_invalid']} coercions={r['coercions']} "
            f"p50={r['latency_p50_s']}s p95={r['latency_p95_s']}s"
        )
    lines.append(f"  (auto_commit = would be WRITTEN: confidence >= {AUTO_COMMIT_THRESHOLD})")
    if len(results) >= 2:
        agr = _agreement(results[0], results[1])
        lines += ["", f"AGREEMENT — {agr['pair']}: kind_agreement={agr['kind_agreement']}"]
        for i, ka, kb in agr["disagreements"][:15]:
            c = cases[i]
            lines.append(
                f"    {c.mention!r}: {results[0]['model']}={ka}  {results[1]['model']}={kb}"
            )
        if len(agr["disagreements"]) > 15:
            lines.append(f"    ... +{len(agr['disagreements']) - 15} more disagreements")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI surface."""
    p = argparse.ArgumentParser(description="Real-corpus (read-only) resolver model probe.")
    p.add_argument("--models", nargs="+", required=True, help="Ollama model tags to probe.")
    p.add_argument("--limit", type=int, default=200, help="Max residue cases to sample.")
    p.add_argument("--host", default=os.environ.get("DOCRAY_OLLAMA_HOST", "http://localhost:11434"))
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--env", type=Path, default=Path(".env"), help="dotenv with DATABASE_URL.")
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="report path (default: $DOCRAY_WORK_DIR/findings/bench/<date>/corpus_probe.txt)",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    """Sample the corpus residue, probe each model read-only, and report."""
    args = _parse_args(argv)
    _load_dotenv(args.env)
    with connect(Config.from_env()) as conn:
        sampled = sample_cases(conn, args.limit)
    cases = [c for _, c in sampled]
    if not cases:
        print("[error] no referential residue found in the corpus.", file=sys.stderr)
        return 1
    print(f"[info] sampled {len(cases)} residue cases from the corpus.", file=sys.stderr)
    results = []
    for model in args.models:
        print(f"[info] probing ollama/{model} ...", file=sys.stderr)
        results.append(score_model(args.host, model, cases, args.timeout))
    report = _report(results, cases)
    if args.out is None:
        args.out = work_dir("findings", "bench", time.strftime("%Y-%m-%d")) / "corpus_probe.txt"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[info] wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
