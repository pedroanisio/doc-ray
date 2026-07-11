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


def make_doc(conn, text: str, *, source_uri: str = "s3://it") -> str:
    sents = build_sentences(text)
    return db.insert_document(
        conn,
        title="itest",
        doc_type="txt",
        source_uri=source_uri,
        content_sha256=unique_sha(),
        byte_size=len(text),
        page_count=None,
        full_text=text,
        status="extracted",
        sentences=sents,
    )


def ensure_admin_principal(conn, principal_id: str, display_name: str) -> None:
    """Idempotently give a fixed test principal_id the admin role.

    Since BL-064, docray_writable_documents()/docray_is_admin() are checked
    against the DB, not just an in-memory Principal.scopes — a test that
    wants a "generic write-scoped principal able to act on any document"
    needs a real principals/principal_roles row, the same way a real admin
    would. Takes a sync connection; callers that also need an async
    connection for the actual mutation should use this one only for setup.
    """
    conn.execute(
        # Unqualified ON CONFLICT: display_name is independently UNIQUE, so a
        # second fixed test principal_id reusing the same display_name must
        # be tolerated too, not just a re-run with the same id.
        "INSERT INTO principals (id, kind, display_name) VALUES (%s, 'service', %s) "
        "ON CONFLICT DO NOTHING",
        (principal_id, display_name),
    )
    admin_role_id = conn.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING",
        (principal_id, admin_role_id),
    )


def make_annotation(conn, doc_id: str, layer: str, tool: str, version: str | None = None) -> str:
    aid = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool, tool_version) "
        "VALUES (%s, %s, %s, %s, %s)",
        (aid, doc_id, layer, tool, version),
    )
    return aid


def span_of(conn, doc_id: str, start: int, end: int) -> str:
    return db.ensure_spans(conn, doc_id, [(start, end)])[(start, end)]


def make_tokens(
    conn, doc_id: str, annotation_id: str, text: str, upos: dict | None = None
) -> list[dict]:
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
        out.append({"id": tid, "surface": surface, "ordinal": i, "span_id": span_map[(s, e)]})
    return out
