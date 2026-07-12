"""Helpers for DB-backed integration tests (not a test module itself).

Builds throwaway documents/annotations/tokens against the live Postgres so each
NLP-layer migration can be exercised end-to-end and cleaned up afterwards.
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from collections.abc import Mapping

import psycopg
from psycopg.rows import dict_row

from doc_ray.adapters import persistence as db
from doc_ray.config import DEFAULT_DATABASE_URL
from doc_ray.domain import lexemes as lex
from doc_ray.domain.sentences import build_sentences
from doc_ray.domain.ulid import new_ulid_str

DB_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
_TEST_DB_PREFIXES = ("docray_test", "pytest_", "test_")
_TEST_HOSTS = {"test-postgres"}
_TEST_PORTS = {15432}

# The worked example from nlp_annotation_layers.md.
EXAMPLE = "Elena founded a startup in Berlin."


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when an integration test is pointed at a live/UAT corpus."""


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _running_in_ci() -> bool:
    return _truthy(os.environ.get("GITHUB_ACTIONS")) or _truthy(os.environ.get("CI"))


def _row_value(row, key: str, index: int = 0):
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


def _looks_like_dedicated_test_database(db_url: str) -> bool:
    parsed = urllib.parse.urlparse(db_url)
    db_name = parsed.path.rsplit("/", 1)[-1]
    username = parsed.username or ""
    host = parsed.hostname or ""
    return (
        db_name.startswith(_TEST_DB_PREFIXES)
        or username.endswith("_test")
        or host in _TEST_HOSTS
        or parsed.port in _TEST_PORTS
    )


def require_safe_test_database(conn, *, db_url: str = DB_URL) -> None:
    """Refuse DB-backed tests when they target a live/UAT corpus."""
    if _truthy(os.environ.get("DOCRAY_TEST_ALLOW_LIVE_DB")):
        return
    if _looks_like_dedicated_test_database(db_url) or _running_in_ci():
        return

    table_row = conn.execute("SELECT to_regclass('public.documents') AS documents_table").fetchone()
    if not table_row or _row_value(table_row, "documents_table") is None:
        return
    count_row = conn.execute(
        """
        SELECT count(*) AS n
        FROM documents
        WHERE NOT (
            title = 'itest'
            AND coalesce(source_uri, '') LIKE 's3://it%'
        )
        """
    ).fetchone()
    non_fixture_count = int(_row_value(count_row, "n"))
    if non_fixture_count:
        raise UnsafeTestDatabaseError(
            "refusing to run DB-backed tests against a database containing "
            f"{non_fixture_count} non-fixture documents; use "
            "scripts/test-in-docker.sh or set DOCRAY_TEST_ALLOW_LIVE_DB=1 "
            "for an explicit operator override"
        )
    raise UnsafeTestDatabaseError(
        "refusing to run DB-backed tests against a non-test database; use "
        "scripts/test-in-docker.sh or set DOCRAY_TEST_ALLOW_LIVE_DB=1 for an "
        "explicit operator override"
    )


def connect() -> psycopg.Connection:
    conn = psycopg.connect(DB_URL, row_factory=dict_row, autocommit=True)
    try:
        require_safe_test_database(conn)
    except Exception:
        conn.close()
        raise
    return conn


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
