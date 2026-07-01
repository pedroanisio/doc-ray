"""Database access layer (psycopg 3)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

import psycopg
from psycopg import errors as pgerr
from psycopg.rows import dict_row

from . import lexemes as _lex
from .config import Config


@contextmanager
def connect(cfg: Config) -> Iterator[psycopg.Connection]:
    # autocommit=True so each `with conn.transaction()` is a real BEGIN/COMMIT.
    # Without it, an earlier SELECT opens an implicit tx and transaction() would
    # only create a savepoint — the insert would roll back silently on close.
    conn = psycopg.connect(cfg.database_url, row_factory=dict_row, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def find_by_sha256(conn: psycopg.Connection, sha256: str) -> Optional[dict[str, Any]]:
    cur = conn.execute(
        "SELECT id, status FROM documents WHERE content_sha256 = %s", (sha256,)
    )
    return cur.fetchone()


def ensure_spans(conn, document_id: str,
                 pairs: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
    """Register every (char_start, char_end) pair as a span for the document
    (idempotent) and return a {(start, end): span_id} map.

    Uses a temp staging table + COPY so a document with hundreds of thousands of
    word spans loads in bulk rather than one INSERT per span.
    """
    uniq = sorted({(int(s), int(e)) for s, e in pairs})
    if not uniq:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _span_stage "
            "(char_start int, char_end int)"
        )
        cur.execute("TRUNCATE _span_stage")
        with cur.copy("COPY _span_stage (char_start, char_end) FROM STDIN") as cp:
            for start, end in uniq:
                cp.write_row((start, end))
        cur.execute(
            "INSERT INTO spans (document_id, char_start, char_end) "
            "SELECT %s, char_start, char_end FROM _span_stage "
            "ON CONFLICT (document_id, char_start, char_end) DO NOTHING",
            (document_id,),
        )
    # Return only the requested pairs (join the staging table), not every span
    # in the document — otherwise this is O(all spans) on every call.
    rows = conn.execute(
        "SELECT s.id, s.char_start, s.char_end FROM spans s "
        "JOIN _span_stage st ON st.char_start=s.char_start AND st.char_end=s.char_end "
        "WHERE s.document_id=%s",
        (document_id,),
    ).fetchall()
    return {(r["char_start"], r["char_end"]): r["id"] for r in rows}


def get_or_create_lexeme(conn, cache: dict, lemma: str, pos: str,
                         lang: str) -> tuple[str, bool]:
    """Resolve (lemma, pos, lang) to a lexeme uid, minting one if new.

    Returns (uid, created). Requires an autocommit connection so a uid
    UniqueViolation (a hash collision between two *different* keys) can be
    caught and resolved by lengthening the alias — mirroring the Registry's
    collision-grow. `cache` memoizes within a run so repeats never hit the DB.
    """
    lemma_n = _lex.norm_lemma(lemma)
    pos_u, lang_l = pos.upper(), lang.lower()
    key = f"{lang_l}:{pos_u}:{lemma_n}"
    hit = cache.get(key)
    if hit is not None:
        return hit, False

    row = conn.execute(
        "SELECT uid FROM lexemes WHERE lang=%s AND pos=%s AND lemma=%s",
        (lang_l, pos_u, lemma_n),
    ).fetchone()
    if row:
        cache[key] = row["uid"]
        return row["uid"], False

    size = 8
    while True:
        uid = _lex.uid_candidate(key, pos_u, lang_l, size)
        try:
            conn.execute(
                "INSERT INTO lexemes (uid, lang, pos, lemma) VALUES (%s, %s, %s, %s)",
                (uid, lang_l, pos_u, lemma_n),
            )
            cache[key] = uid
            return uid, True
        except pgerr.UniqueViolation:
            # Either the identity was inserted concurrently (reuse its uid) or a
            # different key hashed to this uid (grow the alias and retry).
            row = conn.execute(
                "SELECT uid FROM lexemes WHERE lang=%s AND pos=%s AND lemma=%s",
                (lang_l, pos_u, lemma_n),
            ).fetchone()
            if row:
                cache[key] = row["uid"]
                return row["uid"], False
            size = min(size + 2, _lex.HASH_HEX_LEN)


def replace_annotation(conn, *, annotation_id: str, document_id: str,
                       layer: str, tool: str, tool_version: str | None,
                       occurrences: list[tuple[str, str]],
                       provenance: dict) -> None:
    """Atomically (re)write one annotation run: its occurrences and its
    'annotated' provenance event. Replaces any prior run for (doc, layer, tool).
    `occurrences` are (span_id, lexeme_uid) pairs."""
    with conn.transaction():
        conn.execute(
            "DELETE FROM annotations WHERE document_id=%s AND layer=%s AND tool=%s",
            (document_id, layer, tool),
        )
        conn.execute(
            "INSERT INTO annotations (id, document_id, layer, tool, tool_version) "
            "VALUES (%s, %s, %s, %s, %s)",
            (annotation_id, document_id, layer, tool, tool_version),
        )
        if occurrences:
            with conn.cursor().copy(
                "COPY lexeme_occurrences "
                "(annotation_id, span_id, lexeme_uid) FROM STDIN"
            ) as cp:
                for span_id, uid in occurrences:
                    cp.write_row((annotation_id, span_id, uid))

        conn.execute(
            """
            INSERT INTO document_provenance
                (id, document_id, kind, tool, tool_version, actor, note)
            VALUES (%s, %s, 'annotated', %s, %s, %s, %s)
            """,
            (provenance["id"], document_id, provenance["tool"],
             provenance["tool_version"], provenance["actor"], provenance["note"]),
        )
        param_rows = [
            (provenance["id"], k, str(v))
            for k, v in (provenance.get("params") or {}).items() if v is not None
        ]
        if param_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO provenance_params (event_id, key, value) "
                    "VALUES (%s, %s, %s) ON CONFLICT (event_id, key) DO NOTHING",
                    param_rows,
                )


def _author_id(conn: psycopg.Connection, name: str) -> int:
    return conn.execute(
        "INSERT INTO authors (name) VALUES (%s) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    ).fetchone()["id"]


def _tag_id(conn: psycopg.Connection, tag: str) -> int:
    return conn.execute(
        "INSERT INTO tags (tag) VALUES (%s) "
        "ON CONFLICT (tag) DO UPDATE SET tag = EXCLUDED.tag RETURNING id",
        (tag,),
    ).fetchone()["id"]


def insert_document(
    conn: psycopg.Connection,
    *,
    title: str,
    doc_type: str,
    mime_type: Optional[str] = None,
    original_filename: Optional[str] = None,
    source_uri: str,
    content_sha256: str,
    byte_size: int,
    page_count: Optional[int],
    char_count: Optional[int] = None,
    text_sha256: Optional[str] = None,
    full_text: Optional[str],
    status: str,
    error: Optional[str] = None,
    authors: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    identifiers: Optional[dict[str, str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    sentences: Optional[list[dict[str, Any]]] = None,
    provenance: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Insert a document and all its normalized (4NF) satellites — authors,
    tags, identifiers, metadata, content, sentences, provenance — in one
    transaction. Returns the new document id."""
    with conn.transaction():
        row = conn.execute(
            """
            INSERT INTO documents
                (title, doc_type, mime_type, original_filename, source_uri,
                 content_sha256, byte_size, page_count, char_count, text_sha256,
                 status, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                title,
                doc_type,
                mime_type,
                original_filename,
                source_uri,
                content_sha256,
                byte_size,
                page_count,
                char_count,
                text_sha256,
                status,
                error,
            ),
        ).fetchone()
        doc_id = row["id"]

        for position, name in enumerate(authors or []):
            if name:
                conn.execute(
                    "INSERT INTO document_authors (document_id, author_id, position) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (doc_id, _author_id(conn, name), position),
                )

        for tag in tags or []:
            if tag:
                conn.execute(
                    "INSERT INTO document_tags (document_id, tag_id) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (doc_id, _tag_id(conn, tag)),
                )

        for scheme, value in (identifiers or {}).items():
            if value is not None:
                conn.execute(
                    "INSERT INTO document_identifiers (document_id, scheme, value) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (doc_id, scheme, str(value)),
                )

        for key, value in (metadata or {}).items():
            if value is not None:
                conn.execute(
                    "INSERT INTO document_metadata (document_id, key, value) "
                    "VALUES (%s, %s, %s) ON CONFLICT (document_id, key) DO NOTHING",
                    (doc_id, key, str(value)),
                )

        conn.execute(
            "INSERT INTO document_content (document_id, full_text) VALUES (%s, %s)",
            (doc_id, full_text),
        )

        if sentences:
            span_map = ensure_spans(
                conn, doc_id, [(s["char_start"], s["char_end"]) for s in sentences]
            )
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO sentences (id, document_id, ordinal, text, span_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (s["id"], doc_id, s["ordinal"], s["text"],
                         span_map[(s["char_start"], s["char_end"])])
                        for s in sentences
                    ],
                )

        if provenance:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO document_provenance
                        (id, document_id, kind, tool, tool_version, actor,
                         input_sha256, output_sha256, note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            e["id"], doc_id, e["kind"], e["tool"],
                            e["tool_version"], e["actor"], e["input_sha256"],
                            e["output_sha256"], e["note"],
                        )
                        for e in provenance
                    ],
                )
            param_rows = [
                (e["id"], k, str(v))
                for e in provenance
                for k, v in (e.get("params") or {}).items()
                if v is not None
            ]
            if param_rows:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO provenance_params (event_id, key, value) "
                        "VALUES (%s, %s, %s) ON CONFLICT (event_id, key) DO NOTHING",
                        param_rows,
                    )
    return str(doc_id)
