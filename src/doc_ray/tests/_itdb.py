"""Helpers for DB-backed integration tests (not a test module itself).

Builds throwaway documents/annotations/tokens against the live Postgres so each
NLP-layer migration can be exercised end-to-end and cleaned up afterwards.
"""

from __future__ import annotations

import hashlib
import os

import psycopg
from psycopg.rows import dict_row

from doc_ray.adapters import persistence as db
from doc_ray.domain import lexemes as lex
from doc_ray.domain.sentences import build_sentences
from doc_ray.domain.ulid import new_ulid_str

DB_URL = os.environ.get("DATABASE_URL", "postgresql://docray:docray@localhost:5455/docray")

# The worked example from nlp_annotation_layers.md.
EXAMPLE = "Elena founded a startup in Berlin."


def connect() -> psycopg.Connection:
    return psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)


def unique_sha() -> str:
    return hashlib.sha256(os.urandom(16)).hexdigest()


def make_doc(conn, text: str) -> str:
    sents = build_sentences(text)
    return db.insert_document(
        conn, title="itest", doc_type="txt", source_uri="s3://it",
        content_sha256=unique_sha(), byte_size=len(text), page_count=None,
        full_text=text, status="extracted", sentences=sents,
    )


def make_annotation(conn, doc_id: str, layer: str, tool: str,
                    version: str | None = None) -> str:
    aid = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool, tool_version) "
        "VALUES (%s, %s, %s, %s, %s)",
        (aid, doc_id, layer, tool, version),
    )
    return aid


def span_of(conn, doc_id: str, start: int, end: int) -> str:
    return db.ensure_spans(conn, doc_id, [(start, end)])[(start, end)]


def make_tokens(conn, doc_id: str, annotation_id: str, text: str,
                upos: dict | None = None) -> list[dict]:
    """Tokenize `text`, register spans, and insert `tokens` rows.

    Returns [{id, surface, ordinal, span_id}]. Requires migration 008.
    """
    words = list(lex.iter_words(text))
    span_map = db.ensure_spans(conn, doc_id, [(s, e) for _, s, e in words])
    out = []
    for i, (surface, s, e) in enumerate(words):
        tid = new_ulid_str()
        conn.execute(
            "INSERT INTO tokens (id, annotation_id, span_id, ordinal, upos) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, annotation_id, span_map[(s, e)], i, (upos or {}).get(surface)),
        )
        out.append({"id": tid, "surface": surface, "ordinal": i,
                    "span_id": span_map[(s, e)]})
    return out
