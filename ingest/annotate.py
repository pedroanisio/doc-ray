"""Annotate stored documents with the lexeme layer.

Reads each document's canonical full_text from the DB, tokenizes it into word
spans, runs an analyzer (rule-based, zero-dependency by default), upserts the
distinct lexemes, and records the occurrences under an annotation run — plus an
'annotated' provenance event.

Usage:
    python -m ingest.annotate --all
    python -m ingest.annotate --filename AirFrance447_BEA.pdf
    python -m ingest.annotate --all --lang en          # fallback when detection is weak
"""

from __future__ import annotations

import argparse
import sys

from . import db, lexemes as lex
from .config import Config
from .ulid import new_ulid_str


def annotate_document(conn, doc_id: str, analyzer, cache: dict,
                      lang_default: str) -> dict:
    row = conn.execute(
        "SELECT full_text FROM document_content WHERE document_id = %s", (doc_id,)
    ).fetchone()
    full_text = (row["full_text"] if row else None) or ""
    if not full_text:
        return {"occurrences": 0, "lexemes_new": 0, "lang": None}

    lang = lex.detect_language(full_text, default=lang_default)
    words = list(lex.iter_words(full_text))
    analyses = analyzer.analyze([w for w, _, _ in words])

    # Register word spans in bulk, then reference them by span_id.
    span_map = db.ensure_spans(conn, doc_id, [(s, e) for _, s, e in words])

    occurrences: list[tuple[str, str]] = []
    new_lexemes = 0
    for (surface, start, end), (lemma, pos) in zip(words, analyses):
        uid, created = db.get_or_create_lexeme(conn, cache, lemma, pos, lang)
        new_lexemes += created
        occurrences.append((span_map[(start, end)], uid))

    provenance = {
        "id": new_ulid_str(),
        "tool": analyzer.name,
        "tool_version": getattr(analyzer, "version", None),
        "actor": "annotate",
        "note": None,
        "params": {"occurrences": len(occurrences),
                   "lexemes_new": new_lexemes, "lang": lang},
    }
    db.replace_annotation(
        conn, annotation_id=new_ulid_str(), document_id=doc_id,
        layer="lexeme", tool=analyzer.name,
        tool_version=getattr(analyzer, "version", None),
        occurrences=occurrences, provenance=provenance,
    )
    return {"occurrences": len(occurrences), "lexemes_new": new_lexemes, "lang": lang}


def resolve_ids(conn, *, doc_id=None, filename=None) -> list[str]:
    if doc_id:
        return [doc_id]
    if filename:
        rows = conn.execute(
            "SELECT id FROM documents WHERE original_filename=%s ORDER BY created_at",
            (filename,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM documents WHERE status='extracted' ORDER BY created_at"
        ).fetchall()
    return [str(r["id"]) for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Annotate documents with lexemes.")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--id")
    sel.add_argument("--filename")
    sel.add_argument("--all", action="store_true")
    ap.add_argument("--backend", choices=["rule"], default="rule",
                    help="Analyzer backend (only the zero-dep rule engine for now).")
    ap.add_argument("--lang", default="en",
                    help="Fallback language when detection is weak (default: en).")
    args = ap.parse_args(argv)

    analyzer = lex.RuleAnalyzer()
    cache: dict = {}                       # canonical_key -> uid, shared across docs
    cfg = Config.from_env()
    with db.connect(cfg) as conn:
        ids = resolve_ids(conn, doc_id=args.id, filename=args.filename)
        if not ids:
            print("No matching documents.", file=sys.stderr)
            return 1
        total_occ = 0
        for doc_id in ids:
            title = conn.execute(
                "SELECT title FROM documents WHERE id=%s", (doc_id,)
            ).fetchone()["title"]
            r = annotate_document(conn, doc_id, analyzer, cache, args.lang)
            total_occ += r["occurrences"]
            print(f"  {title[:34]:34}  lang={r['lang']}  "
                  f"{r['occurrences']:>6} occurrences  (+{r['lexemes_new']} lexemes)")
        distinct = conn.execute("SELECT count(*) n FROM lexemes").fetchone()["n"]
        print(f"Done: {total_occ:,} occurrences, {distinct:,} distinct lexemes "
              f"[{analyzer.name}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
