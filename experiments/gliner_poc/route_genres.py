#!/usr/bin/env python3
"""Genre routing: classify each document, then extract with a label set that fits it.

THE PROBLEM THIS SOLVES
    A single technical label set applied corpus-wide produces confident nonsense
    on the documents that are not technical. Measured on this corpus: GLiNER
    labelled ``The Stolen Journals`` — an artifact in Dune — as ``dataset`` with
    0.90 confidence, and lexical search for ``transformer`` returned General
    Electric switchgears from a business strategy book. Only about 8 of 354
    documents carry substantive ML content, so the mismatch is the normal case,
    not an edge case.

    An entity layer that confidently mislabels is worse than one that abstains:
    canonicalization resolves surfaces to shared identities, so a false
    ``dataset`` becomes a false link threaded through the corpus — the exact
    structure PURPOSE.md says everything else rests on.

THE FIX
    Route before you extract. Classify each document into a genre, then hand
    GLiNER only the label set that genre can legitimately produce. A fiction
    document is never offered ``dataset``, so it can never return one.

VERIFICATION (PALS's Law — the classifier is an LLM and is untrusted by default)
    Each document is classified TWICE from disjoint sentence samples. Agreement
    is required. A document whose two votes disagree is marked ``uncertain`` and
    routed to the generic label set rather than a guessed one — abstention is a
    correct outcome here, not a failure.

Read-only against the corpus. Writes nothing; prints the routing table.

Usage::

    python route_genres.py --limit 40
    python route_genres.py --limit 354 --json > routing.json

Exit codes: 0 routed; 2 no documents or the model host was unreachable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

import psycopg

#: Genre taxonomy. Deliberately coarse: the routing only has to be right enough
#: to pick a label set, and a fine-grained taxonomy multiplies the ways the
#: classifier can disagree with itself for no gain in the extraction that follows.
GENRES = (
    "machine learning and AI",
    "software engineering",
    "mathematics",
    "natural science",
    "business and management",
    "humanities and social science",
    "fiction and literature",
    "reference and manual",
)

#: Genre -> the entity types GLiNER may return for a document of that genre.
#: A type absent here cannot be produced, which is the whole mechanism: fiction
#: is never offered "dataset", so "The Stolen Journals" cannot become one.
LABEL_SETS: dict[str, list[str]] = {
    "machine learning and AI": [
        "machine learning model",
        "dataset",
        "benchmark",
        "neural network architecture",
        "evaluation metric",
        "software library",
        "algorithm",
    ],
    "software engineering": [
        "software library",
        "programming language",
        "framework",
        "design pattern",
        "software tool",
    ],
    "mathematics": ["theorem", "mathematical object", "proof technique", "notation"],
    "natural science": ["scientific method", "measured quantity", "material", "organism"],
    "business and management": ["company", "product", "market", "business strategy", "job role"],
    "humanities and social science": [
        "historical period",
        "school of thought",
        "person",
        "place",
        "published work",
    ],
    "fiction and literature": [
        "fictional character",
        "fictional place",
        "fictional organization",
        "fictional artifact",
        "published work",
    ],
    "reference and manual": ["software tool", "procedure", "product", "standard"],
    # Used when the two classification votes disagree. Generic on purpose: the
    # honest response to "we do not know the genre" is to claim less, not to
    # pick one and extract confidently against it.
    "uncertain": ["person", "organization", "place", "published work"],
}


def fetch_documents(conn: psycopg.Connection[Any], limit: int) -> list[dict[str, Any]]:
    """Return documents with two disjoint sentence samples each.

    The two samples come from different regions of the document (early and
    middle) so the paired votes are genuinely independent evidence rather than
    the same passage scored twice.
    """
    sql = """
        SELECT d.id::text, coalesce(d.title, d.original_filename, '(untitled)'),
               (SELECT string_agg(t, ' ') FROM (
                   SELECT text AS t FROM sentences
                   WHERE document_id = d.id AND length(text) BETWEEN 60 AND 400
                   ORDER BY ordinal LIMIT 6
               ) early),
               (SELECT string_agg(t, ' ') FROM (
                   SELECT text AS t FROM sentences
                   WHERE document_id = d.id AND length(text) BETWEEN 60 AND 400
                   ORDER BY ordinal OFFSET 400 LIMIT 6
               ) middle)
        FROM documents d
        ORDER BY d.id
        LIMIT %(limit)s
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"limit": limit})
        return [
            {"id": r[0], "title": r[1], "sample_a": r[2] or "", "sample_b": r[3] or ""}
            for r in cur.fetchall()
        ]


def classify(host: str, model: str, title: str, sample: str) -> str:
    """Ask the local model which genre a passage belongs to.

    Constrained to the taxonomy and read back through an exact-match check: a
    reply outside GENRES is discarded rather than coerced, because a coerced
    label is indistinguishable from a correct one downstream.
    """
    options = "\n".join(f"- {g}" for g in GENRES)
    prompt = (
        "Classify the document into exactly ONE genre from this list.\n"
        f"{options}\n\n"
        f"Title: {title}\n"
        f"Excerpt: {sample[:1800]}\n\n"
        "Reply with the genre text only, copied exactly from the list. No explanation."
    )
    payload = json.dumps(
        {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0}}
    ).encode()
    request = urllib.request.Request(
        f"{host}/api/generate", data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        answer = json.load(response).get("response", "").strip().lower()
    # Case-fold BOTH sides. "machine learning and AI" is the only genre carrying
    # an uppercase letter, so comparing it raw against a lower-cased reply failed
    # for that genre and no other — which sent every ML paper, the exact documents
    # this router exists to classify, down the "unparseable" path.
    for genre in GENRES:
        if genre.lower() in answer:
            return genre
    return "unparseable"


def route(documents: list[dict[str, Any]], host: str, model: str) -> list[dict[str, Any]]:
    """Classify each document twice and keep only agreeing verdicts."""
    routed: list[dict[str, Any]] = []
    for index, document in enumerate(documents, start=1):
        vote_a = classify(host, model, document["title"], document["sample_a"])
        vote_b = (
            classify(host, model, document["title"], document["sample_b"])
            if document["sample_b"]
            else vote_a
        )
        agreed = vote_a == vote_b and vote_a in LABEL_SETS
        genre = vote_a if agreed else "uncertain"
        routed.append(
            {
                "id": document["id"],
                "title": document["title"][:70],
                "vote_a": vote_a,
                "vote_b": vote_b,
                "genre": genre,
                "verified": agreed,
                "labels": LABEL_SETS[genre],
            }
        )
        print(f"  [{index}/{len(documents)}] {document['title'][:52]:<52} -> {genre}", flush=True)
    return routed


def main(argv: list[str] | None = None) -> int:
    """Route documents to label sets and report the table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=40, help="Documents to classify.")
    parser.add_argument(
        "--ollama-host", default=os.environ.get("LAB_OLLAMA_HOST", "http://ollama:11434")
    )
    parser.add_argument("--model", default=os.environ.get("LAB_OLLAMA_MODEL", "qwen2.5:14b"))
    parser.add_argument("--json", action="store_true", help="Emit the routing table as JSON.")
    args = parser.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is unset", file=sys.stderr)
        return 2

    with psycopg.connect(dsn) as conn:
        conn.execute("SET ROLE docray_batch")
        documents = fetch_documents(conn, args.limit)
    if not documents:
        print("no documents", file=sys.stderr)
        return 2

    try:
        routed = route(documents, args.ollama_host.rstrip("/"), args.model)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"model host {args.ollama_host} unreachable: {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(routed, sys.stdout, indent=2)
        print()
        return 0

    genres = Counter(r["genre"] for r in routed)
    verified = sum(1 for r in routed if r["verified"])
    print(f"\nRouted {len(routed)} documents — {verified} verified by agreeing votes, "
          f"{len(routed) - verified} uncertain\n")
    for genre, count in genres.most_common():
        print(f"  {genre:<32} {count:>4}")
    print("\nDisagreements (routed to the generic label set):")
    for row in routed:
        if not row["verified"]:
            print(f"  {row['title'][:56]:<56} {row['vote_a']} | {row['vote_b']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
