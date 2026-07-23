"""Database access layer (psycopg 3)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import errors as pgerr
from psycopg.rows import dict_row

from doc_ray.adapters.persistence_doc_admin import (
    _author_id,
    _tag_id,
)
from doc_ray.adapters.persistence_doc_admin import (
    add_document_tags as add_document_tags,
)
from doc_ray.adapters.persistence_doc_admin import (
    set_document_lifecycle as set_document_lifecycle,
)
from doc_ray.adapters.persistence_doc_admin import (
    update_document_metadata as update_document_metadata,
)
from doc_ray.adapters.persistence_geometry import (
    _update_span_geometry_from_layout,
)
from doc_ray.adapters.persistence_geometry import (
    layout_runs_from_page_runs as layout_runs_from_page_runs,
)
from doc_ray.adapters.persistence_geometry import (
    page_rows_from_page_metrics as page_rows_from_page_metrics,
)
from doc_ray.adapters.persistence_geometry import (
    replace_document_layout_runs as replace_document_layout_runs,
)
from doc_ray.adapters.persistence_geometry import (
    replace_document_pages as replace_document_pages,
)
from doc_ray.adapters.persistence_geometry import (
    span_geometry_from_layout_rows as span_geometry_from_layout_rows,
)
from doc_ray.config import Config
from doc_ray.domain import lexemes as _lex

#: A psycopg connection whose rows are mappings (``row_factory=dict_row``).
DictConnection = psycopg.Connection[dict[str, Any]]


@contextmanager
def connect(cfg: Config) -> Iterator[DictConnection]:
    """Open a dict-row psycopg connection, yielding it and closing it on exit.

    The connection is opened with ``autocommit=True`` so each
    ``with conn.transaction()`` is a real ``BEGIN``/``COMMIT``. Without it, an
    earlier ``SELECT`` opens an implicit transaction and ``transaction()`` would
    only create a savepoint — the insert would roll back silently on close.

    The connection drops to ``docray_batch``, the trusted batch-writer role the
    RLS policies name (migration 106). Every caller of this helper is
    batch/CLI work — the worker, ingest, reannotation, export, health — running
    with no request principal on whose behalf it could act, and spanning every
    tenant. Before 106 those writes worked only because the connection happened
    to own the tables and Postgres exempts an owner from unforced RLS; now the
    exemption is granted explicitly, so a path that forgets to assume a role
    fails closed instead of silently reading everything.

    Args:
        cfg: Runtime configuration carrying the database URL.

    Yields:
        An open connection with a ``dict_row`` row factory.
    """
    conn = psycopg.connect(cfg.database_url, row_factory=dict_row, autocommit=True)
    try:
        conn.execute("SET ROLE docray_batch")
        yield conn
    finally:
        conn.close()


def find_by_sha256(conn: DictConnection, sha256: str) -> dict[str, Any] | None:
    """Look up a document by its content hash.

    Args:
        conn: Open database connection.
        sha256: Hex-encoded SHA-256 of the document content.

    Returns:
        A ``{id, status}`` mapping for the matching document, or ``None``.
    """
    cur = conn.execute("SELECT id, status FROM documents WHERE content_sha256 = %s", (sha256,))
    return cur.fetchone()


def ensure_spans(
    conn: DictConnection, document_id: str, pairs: list[tuple[int, int]]
) -> dict[tuple[int, int], str]:
    """Register ``(char_start, char_end)`` pairs as spans and map them to ids.

    Every pair is registered as a span for the document (idempotent). Uses a
    temp staging table + ``COPY`` so a document with hundreds of thousands of
    word spans loads in bulk rather than one ``INSERT`` per span.

    Args:
        conn: Open database connection.
        document_id: Document the spans belong to.
        pairs: ``(char_start, char_end)`` offset pairs to register.

    Returns:
        A ``{(start, end): span_id}`` map for exactly the requested pairs.
    """
    uniq = sorted({(int(s), int(e)) for s, e in pairs})
    if not uniq:
        return {}
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE IF NOT EXISTS _span_stage (char_start int, char_end int)")
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
        _update_span_geometry_from_layout(conn, document_id, uniq)
    # Return only the requested pairs (join the staging table), not every span
    # in the document — otherwise this is O(all spans) on every call.
    rows = conn.execute(
        "SELECT s.id, s.char_start, s.char_end FROM spans s "
        "JOIN _span_stage st ON st.char_start=s.char_start AND st.char_end=s.char_end "
        "WHERE s.document_id=%s",
        (document_id,),
    ).fetchall()
    return {(r["char_start"], r["char_end"]): r["id"] for r in rows}


def get_or_create_lexeme(
    conn: DictConnection,
    cache: dict[str, str],
    lemma: str,
    pos: str,
    lang: str,
) -> tuple[str, bool]:
    """Resolve ``(lemma, pos, lang)`` to a lexeme uid, minting one if new.

    Requires an autocommit connection so a uid ``UniqueViolation`` (a hash
    collision between two *different* keys) can be caught and resolved by
    lengthening the alias — mirroring the Registry's collision-grow. ``cache``
    memoizes within a run so repeats never hit the DB.

    Args:
        conn: Open autocommit database connection.
        cache: In-run memo mapping canonical key to uid.
        lemma: Surface lemma to normalize and resolve.
        pos: Part-of-speech tag (upper-cased for the key).
        lang: Language code (lower-cased for the key).

    Returns:
        A ``(uid, created)`` pair where ``created`` is ``True`` iff a new lexeme
        row was minted on this call.
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


def replace_annotation(
    conn: DictConnection,
    *,
    annotation_id: str,
    document_id: str,
    layer: str,
    tool: str,
    tool_version: str | None,
    occurrences: list[tuple[str, str]],
    provenance: dict[str, Any],
) -> None:
    """Atomically (re)write one annotation run and its provenance event.

    Replaces any prior run for ``(document_id, layer, tool)`` and records an
    ``'annotated'`` provenance event, all within a single transaction.

    Args:
        conn: Open database connection.
        annotation_id: Id for the new annotation run.
        document_id: Document being annotated.
        layer: Annotation layer name (e.g. ``"lexeme"``).
        tool: Tool that produced the annotation.
        tool_version: Version string of the tool, if known.
        occurrences: ``(span_id, lexeme_uid)`` pairs to record.
        provenance: Provenance event fields, including an optional ``params``
            mapping.
    """
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
                "COPY lexeme_occurrences (annotation_id, span_id, lexeme_uid) FROM STDIN"
            ) as cp:
                for span_id, uid in occurrences:
                    cp.write_row((annotation_id, span_id, uid))

        conn.execute(
            """
            INSERT INTO document_provenance
                (id, document_id, kind, tool, tool_version, actor, note)
            VALUES (%s, %s, 'annotated', %s, %s, %s, %s)
            """,
            (
                provenance["id"],
                document_id,
                provenance["tool"],
                provenance["tool_version"],
                provenance["actor"],
                provenance["note"],
            ),
        )
        param_rows = [
            (provenance["id"], k, str(v))
            for k, v in (provenance.get("params") or {}).items()
            if v is not None
        ]
        if param_rows:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO provenance_params (event_id, key, value) "
                    "VALUES (%s, %s, %s) ON CONFLICT (event_id, key) DO NOTHING",
                    param_rows,
                )


def insert_document(
    conn: DictConnection,
    *,
    title: str,
    doc_type: str,
    mime_type: str | None = None,
    original_filename: str | None = None,
    source_uri: str,
    content_sha256: str,
    byte_size: int,
    page_count: int | None,
    char_count: int | None = None,
    text_sha256: str | None = None,
    full_text: str | None,
    status: str,
    error: str | None = None,
    authors: list[str] | None = None,
    tags: list[str] | None = None,
    identifiers: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
    sentences: list[dict[str, Any]] | None = None,
    provenance: list[dict[str, Any]] | None = None,
    page_runs: list[dict[str, Any]] | None = None,
    page_metrics: list[dict[str, Any]] | None = None,
    owner_principal_id: str | None = None,
) -> str:
    """Insert a document and all its normalized (4NF) satellites in one txn.

    Persists the document alongside its authors, tags, identifiers, metadata,
    content, sentences, and provenance within a single transaction.

    Args:
        conn: Open database connection.
        title: Document title.
        doc_type: Document type discriminator.
        mime_type: MIME type of the source, if known.
        original_filename: Original source filename, if any.
        source_uri: Canonical URI of the stored source bytes.
        content_sha256: Hex SHA-256 of the source content.
        byte_size: Size of the source content in bytes.
        page_count: Page count, if applicable.
        char_count: Character count of the extracted text, if known.
        text_sha256: Hex SHA-256 of the extracted text, if known.
        full_text: Full extracted text.
        status: Document processing status.
        error: Error message if extraction failed.
        authors: Ordered author names.
        tags: Free-form tags.
        identifiers: ``{scheme: value}`` external identifiers.
        metadata: Arbitrary ``{key: value}`` metadata.
        sentences: Sentence records with ``char_start``/``char_end`` offsets.
        provenance: Provenance event records, each with optional ``params``.
        page_runs: Extracted layout rows with page and bbox data, when available.
        page_metrics: Extracted per-page geometry, including pages without text.
        owner_principal_id: Principal the document belongs to (migration 102).
            ``None`` leaves it unowned — admin-only, and reserved for the org
            corpus — which is what operator CLI ingest deliberately produces.

    Returns:
        The id of the newly inserted document.
    """
    with conn.transaction():
        row = conn.execute(
            """
            INSERT INTO documents
                (title, doc_type, mime_type, original_filename, source_uri,
                 content_sha256, byte_size, page_count, char_count, text_sha256,
                 status, error, owner_principal_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                owner_principal_id,
            ),
        ).fetchone()
        assert row is not None  # RETURNING guarantees a row
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
        replace_document_pages(conn, doc_id, page_metrics)
        replace_document_layout_runs(conn, doc_id, full_text, page_runs)

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
                        (
                            s["id"],
                            doc_id,
                            s["ordinal"],
                            s["text"],
                            span_map[(s["char_start"], s["char_end"])],
                        )
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
                            e["id"],
                            doc_id,
                            e["kind"],
                            e["tool"],
                            e["tool_version"],
                            e["actor"],
                            e["input_sha256"],
                            e["output_sha256"],
                            e["note"],
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
