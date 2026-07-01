"""Document embedding loader (feature-hashing bag-of-words -> pgvector).

Computes a deterministic 768-dim signed feature-hashing embedding for each
document's text and stores it against a whole-document span. This is a real,
reproducible embedding (documents sharing vocabulary land near each other under
cosine distance) — it is lexical, not neural; the model tag says so.

Usage:  python -m ingest.embed --all
"""

from __future__ import annotations

import argparse
import hashlib
import sys

import numpy as np

from . import db, lexemes as lex
from .config import Config

DIM = 768
MODEL = "hash-bow@768"


def embed_text(text: str, dim: int = DIM) -> np.ndarray:
    v = np.zeros(dim, dtype=np.float32)
    for word, _, _ in lex.iter_words(text):
        h = int.from_bytes(
            hashlib.blake2b(word.casefold().encode("utf-8"), digest_size=8).digest(),
            "big",
        )
        idx = h % dim
        sign = 1.0 if (h >> 63) & 1 else -1.0
        v[idx] += sign
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


def _to_pgvector(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v.tolist()) + "]"


def embed_document(conn, doc_id: str) -> bool:
    row = conn.execute(
        "SELECT full_text, char_count FROM documents d "
        "JOIN document_content c ON c.document_id=d.id WHERE d.id=%s", (doc_id,)
    ).fetchone()
    text = (row["full_text"] if row else None) or ""
    if not text.strip():
        return False
    span_id = db.ensure_spans(conn, doc_id, [(0, len(text))])[(0, len(text))]
    vec = _to_pgvector(embed_text(text))
    conn.execute(
        "INSERT INTO embedding (span_id, model, vector) VALUES (%s,%s,%s) "
        "ON CONFLICT (span_id, model) DO UPDATE SET vector=EXCLUDED.vector",
        (span_id, MODEL, vec),
    )
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Embed documents into pgvector.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--id"); g.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)
    with db.connect(Config.from_env()) as conn:
        ids = ([args.id] if args.id else
               [str(r["id"]) for r in conn.execute(
                   "SELECT id FROM documents WHERE status='extracted' ORDER BY created_at").fetchall()])
        n = sum(embed_document(conn, d) for d in ids)
        print(f"Embedded {n} document(s) [{MODEL}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
