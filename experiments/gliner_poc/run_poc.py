#!/usr/bin/env python3
"""GLiNER proof of concept: can zero-shot NER name the concepts this corpus is about?

The corpus runs spaCy's ``en_core_web_trf`` entity layer, whose ontology is
PERSON / ORG / GPE / DATE / CARDINAL and friends. For a library of AI papers and
programming books, the entities that actually connect one document to another are
models, datasets, algorithms, benchmarks, libraries and architectures — none of
which that ontology can express. ``PURPOSE.md`` calls entities the connective
tissue of the corpus, so what the entity layer cannot name, the corpus cannot
connect.

GLiNER does zero-shot NER against arbitrary type names supplied at inference, so
the hypothesis is testable without training anything: run both extractors over
the SAME sentences and compare what each can see.

Read-only. Nothing is written to the corpus — this measures, it does not ingest.

Usage::

    python run_poc.py                       # 20 documents x 25 sentences
    python run_poc.py --docs 50 --per-doc 40 --threshold 0.4
    python run_poc.py --json > result.json

Exit codes: 0 the comparison ran; 2 no sentences matched the sample criteria.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import psycopg

#: Entity types this corpus is actually about. Supplied to GLiNER at inference —
#: changing this list needs no retraining, which is the whole point of the test.
DEFAULT_LABELS = [
    "machine learning model",
    "dataset",
    "algorithm",
    "benchmark",
    "software library",
    "neural network architecture",
    "evaluation metric",
    "programming language",
    "research task",
]

#: spaCy types that carry no domain meaning here. Kept separate from the report's
#: raw counts: they are excluded only from the "expressive overlap" comparison,
#: never silently dropped from the totals.
GENERIC_SPACY_TYPES = frozenset(
    {"CARDINAL", "ORDINAL", "DATE", "TIME", "PERCENT", "MONEY", "QUANTITY"}
)


@dataclass
class Sentence:
    """One sampled corpus sentence and the entities each extractor found in it."""

    sentence_id: str
    document_id: str
    text: str
    spacy_entities: list[tuple[str, str]] = field(default_factory=list)
    gliner_entities: list[tuple[str, str, float]] = field(default_factory=list)


def sample_sentences(conn: psycopg.Connection[Any], docs: int, per_doc: int) -> list[Sentence]:
    """Return ``per_doc`` sentences from each of ``docs`` documents.

    A lateral join over an indexed document list, rather than ``ORDER BY
    random()`` over 1.4M rows: the production cluster this reads from is shared
    with the serving tier, and a POC has no business seq-scanning it.
    """
    sql = """
        WITH sampled AS (
            SELECT id FROM documents ORDER BY id LIMIT %(docs)s
        )
        SELECT s.id::text, s.document_id::text, s.text
        FROM sampled d
        JOIN LATERAL (
            SELECT id, document_id, text
            FROM sentences
            WHERE document_id = d.id
              AND length(text) BETWEEN 80 AND 600
            ORDER BY ordinal
            LIMIT %(per_doc)s
        ) s ON true
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"docs": docs, "per_doc": per_doc})
        return [Sentence(sentence_id=r[0], document_id=r[1], text=r[2]) for r in cur.fetchall()]


def attach_spacy_entities(conn: psycopg.Connection[Any], sentences: list[Sentence]) -> None:
    """Populate each sentence's existing spaCy entity mentions, in place.

    An entity belongs to a sentence when its span is contained by the sentence's
    span in the same document; ``entity_mention`` carries no sentence key of its
    own. Bounded by the sampled ids so the containment join never runs corpus-wide.
    """
    by_id = {s.sentence_id: s for s in sentences}
    sql = """
        SELECT s.id::text, em.entity_type, em.surface
        FROM sentences s
        JOIN spans ss ON ss.id = s.span_id
        JOIN spans es ON es.document_id = ss.document_id
                     AND es.char_start >= ss.char_start
                     AND es.char_end   <= ss.char_end
        JOIN entity_mention em ON em.span_id = es.id
        WHERE s.id = ANY(%(ids)s::ulid[])
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"ids": list(by_id)})
        for sentence_id, entity_type, surface in cur.fetchall():
            target = by_id.get(sentence_id)
            if target is not None:
                target.spacy_entities.append((entity_type, surface))


def load_gliner(model_name: str) -> Any:
    """Load GLiNER onto the GPU, failing loudly rather than silently using CPU.

    A CPU fallback here would not be a degraded result, it would be a misleading
    one: the point of running this on an H200 is to establish the throughput a
    real backfill would have.
    """
    import torch
    from gliner import GLiNER

    if not torch.cuda.is_available():
        raise RuntimeError(
            "no CUDA device visible — run with `--device nvidia.com/gpu=all`. "
            "Refusing to fall back to CPU, which would report a throughput "
            "figure that says nothing about a real backfill."
        )
    model = GLiNER.from_pretrained(model_name)
    model = model.to("cuda")
    model.eval()
    return model


def run_gliner(
    model: Any, sentences: list[Sentence], labels: list[str], threshold: float, batch_size: int
) -> float:
    """Extract entities into each sentence, returning wall-clock seconds.

    Uses the batched entry point when the installed gliner exposes one — the
    per-text loop leaves most of an H200 idle — and falls back to sequential
    calls otherwise, so the POC runs on either API.
    """
    started = time.monotonic()
    texts = [s.text for s in sentences]
    batched = getattr(model, "batch_predict_entities", None)

    if callable(batched):
        for offset in range(0, len(texts), batch_size):
            window = texts[offset : offset + batch_size]
            for local_index, found in enumerate(batched(window, labels, threshold=threshold)):
                target = sentences[offset + local_index]
                target.gliner_entities = [
                    (e["label"], e["text"], float(e.get("score", 0.0))) for e in found
                ]
    else:
        for sentence in sentences:
            found = model.predict_entities(sentence.text, labels, threshold=threshold)
            sentence.gliner_entities = [
                (e["label"], e["text"], float(e.get("score", 0.0))) for e in found
            ]
    return time.monotonic() - started


def build_report(sentences: list[Sentence], elapsed: float, labels: list[str]) -> dict[str, Any]:
    """Summarise both extractors over the same sentences."""
    spacy_types: Counter[str] = Counter()
    gliner_types: Counter[str] = Counter()
    spacy_surfaces: dict[str, Counter[str]] = {}
    gliner_surfaces: dict[str, Counter[str]] = {}

    for sentence in sentences:
        for entity_type, surface in sentence.spacy_entities:
            spacy_types[entity_type] += 1
            spacy_surfaces.setdefault(entity_type, Counter())[surface] += 1
        for label, surface, _score in sentence.gliner_entities:
            gliner_types[label] += 1
            gliner_surfaces.setdefault(label, Counter())[surface] += 1

    spacy_domain = {t: n for t, n in spacy_types.items() if t not in GENERIC_SPACY_TYPES}
    only_gliner = sorted(
        {s for c in gliner_surfaces.values() for s in c}
        - {s for c in spacy_surfaces.values() for s in c}
    )

    return {
        "sentences": len(sentences),
        "documents": len({s.document_id for s in sentences}),
        "labels": labels,
        "elapsed_seconds": round(elapsed, 2),
        "sentences_per_second": round(len(sentences) / elapsed, 1) if elapsed > 0 else None,
        "spacy": {
            "total_mentions": sum(spacy_types.values()),
            "by_type": dict(spacy_types.most_common()),
            "domain_typed_mentions": sum(spacy_domain.values()),
            "examples": {
                t: [s for s, _ in c.most_common(5)] for t, c in sorted(spacy_surfaces.items())
            },
        },
        "gliner": {
            "total_mentions": sum(gliner_types.values()),
            "by_type": dict(gliner_types.most_common()),
            "examples": {
                t: [s for s, _ in c.most_common(5)] for t, c in sorted(gliner_surfaces.items())
            },
        },
        "surfaces_only_gliner_found": only_gliner[:60],
        "surfaces_only_gliner_found_total": len(only_gliner),
    }


def print_report(report: dict[str, Any]) -> None:
    """Render the comparison for a human reader."""
    print(
        f"\nSampled {report['sentences']} sentences from {report['documents']} documents"
        f"  |  GLiNER: {report['elapsed_seconds']}s "
        f"({report['sentences_per_second']} sentences/s on GPU)\n"
    )
    print(f"{'spaCy (en_core_web_trf)':<38} {'GLiNER (zero-shot)':<38}")
    print("-" * 76)
    spacy_rows = list(report["spacy"]["by_type"].items())
    gliner_rows = list(report["gliner"]["by_type"].items())
    for index in range(max(len(spacy_rows), len(gliner_rows))):
        left = f"{spacy_rows[index][0]:<28} {spacy_rows[index][1]:>6}" if index < len(spacy_rows) else ""
        right = (
            f"{gliner_rows[index][0]:<28} {gliner_rows[index][1]:>6}"
            if index < len(gliner_rows)
            else ""
        )
        print(f"{left:<38} {right:<38}")
    print("-" * 76)
    print(
        f"{'TOTAL ' + str(report['spacy']['total_mentions']):<38} "
        f"{'TOTAL ' + str(report['gliner']['total_mentions']):<38}"
    )
    print(
        f"\nspaCy mentions carrying a domain-meaningful type: "
        f"{report['spacy']['domain_typed_mentions']}"
    )
    print("\nSample GLiNER extractions by type:")
    for label, examples in report["gliner"]["examples"].items():
        print(f"  {label:<30} {', '.join(examples)}")
    print(
        f"\nDistinct surfaces GLiNER found that spaCy did not: "
        f"{report['surfaces_only_gliner_found_total']}"
    )
    for surface in report["surfaces_only_gliner_found"][:25]:
        print(f"  - {surface}")


def print_side_by_side(sentences: list[Sentence], limit: int, width: int) -> None:
    """Print each sentence with both extractors' output, for manual quality review.

    Only sentences where at least one extractor fired are shown: a page of empty
    rows tells the reader nothing about precision, which is what this view exists
    to expose.
    """
    shown = 0
    # GLiNER-firing sentences first: this view exists to judge GLiNER's precision,
    # and rows where it stayed silent say nothing about that.
    ordered = sorted(sentences, key=lambda s: not s.gliner_entities)
    for sentence in ordered:
        if not sentence.spacy_entities and not sentence.gliner_entities:
            continue
        shown += 1
        if shown > limit:
            break
        text = " ".join(sentence.text.split())
        print(f"\n[{shown}] doc {sentence.document_id[:8]}  sentence {sentence.sentence_id[-8:]}")
        print(f'    "{text[:width]}{"…" if len(text) > width else ""}"')

        spacy_rendered = (
            ", ".join(f"{t}={s}" for t, s in sentence.spacy_entities) or "(none)"
        )
        gliner_rendered = (
            ", ".join(f"{lbl}={s} [{sc:.2f}]" for lbl, s, sc in sentence.gliner_entities)
            or "(none)"
        )
        print(f"    spaCy  : {spacy_rendered}")
        print(f"    GLiNER : {gliner_rendered}")
    print(f"\n({shown - 1 if shown > limit else shown} sentences shown)")


def main(argv: list[str] | None = None) -> int:
    """Sample, extract with both, and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=int, default=20, help="Documents to sample from.")
    parser.add_argument("--per-doc", type=int, default=25, help="Sentences per document.")
    parser.add_argument("--model", default="urchade/gliner_large-v2.1", help="GLiNER model id.")
    parser.add_argument("--threshold", type=float, default=0.5, help="Confidence floor.")
    parser.add_argument("--batch-size", type=int, default=32, help="Sentences per GPU batch.")
    parser.add_argument("--labels", nargs="*", default=None, help="Override the entity types.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    parser.add_argument(
        "--side-by-side",
        type=int,
        default=0,
        help="Show N sentences with both extractors' output, for manual review.",
    )
    parser.add_argument("--width", type=int, default=190, help="Sentence display width.")
    args = parser.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is unset", file=sys.stderr)
        return 2

    labels = args.labels or DEFAULT_LABELS

    with psycopg.connect(dsn) as conn:
        # The corpus tables force RLS; the batch role is the trusted reader the
        # application itself drops to (adapters/persistence.connect).
        conn.execute("SET ROLE docray_batch")
        sentences = sample_sentences(conn, args.docs, args.per_doc)
        if not sentences:
            print("no sentences matched the sample criteria", file=sys.stderr)
            return 2
        attach_spacy_entities(conn, sentences)

    model = load_gliner(args.model)
    elapsed = run_gliner(model, sentences, labels, args.threshold, args.batch_size)
    report = build_report(sentences, elapsed, labels)

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print_report(report)
    if args.side_by_side:
        print_side_by_side(sentences, args.side_by_side, args.width)
    return 0


if __name__ == "__main__":
    sys.exit(main())
