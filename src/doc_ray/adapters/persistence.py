"""Database access layer (psycopg 3)."""

from __future__ import annotations

import bisect
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import errors as pgerr
from psycopg.rows import dict_row

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

    Args:
        cfg: Runtime configuration carrying the database URL.

    Yields:
        An open connection with a ``dict_row`` row factory.
    """
    conn = psycopg.connect(cfg.database_url, row_factory=dict_row, autocommit=True)
    try:
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


def layout_runs_from_page_runs(
    full_text: str, page_runs: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Convert extracted PDF layout runs into durable text-offset rows.

    ``Extracted.page_runs`` is intentionally extractor-local: PyMuPDF reports
    zero-based pages and geometry, while the rest of doc-ray addresses text by
    half-open character spans. This helper bridges the two representations by
    finding each extracted line in the final persisted ``full_text``.
    """
    if not full_text or not page_runs:
        return []

    rows: list[dict[str, Any]] = []
    cursor = 0
    for run in page_runs:
        text = str(run.get("text") or "")
        if not text:
            continue
        bbox = run.get("bbox")
        if bbox is None or len(bbox) != 4:
            continue
        start = full_text.find(text, cursor)
        if start < 0:
            start = full_text.find(text)
        if start < 0:
            continue
        end = start + len(text)
        rows.append(
            {
                "ordinal": len(rows),
                "page_number": int(run.get("page", 0)) + 1,
                "page_width": float(run["page_width"]) if run.get("page_width") else None,
                "page_height": float(run["page_height"]) if run.get("page_height") else None,
                "char_start": start,
                "char_end": end,
                "bbox_x0": float(bbox[0]),
                "bbox_y0": float(bbox[1]),
                "bbox_x1": float(bbox[2]),
                "bbox_y1": float(bbox[3]),
                "text": text,
            }
        )
        cursor = end
    return rows


def page_rows_from_page_metrics(
    page_metrics: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Map zero-based extracted page metrics to database page rows."""
    rows: list[dict[str, Any]] = []
    for metric in page_metrics or []:
        page_width = float(metric["page_width"])
        page_height = float(metric["page_height"])
        text_run_count = int(metric.get("text_run_count") or 0)
        rows.append(
            {
                "page_number": int(metric["page"]) + 1,
                "page_width": page_width,
                "page_height": page_height,
                "has_text": bool(metric.get("has_text")) or text_run_count > 0,
                "text_run_count": text_run_count,
            }
        )
    return rows


def span_geometry_from_layout_rows(
    pairs: list[tuple[int, int]], layout_rows: list[dict[str, Any]]
) -> dict[tuple[int, int], dict[str, Any]]:
    """Return the best page/bbox geometry for each requested span range.

    Scanning every layout row per span is O(spans x rows) — for a book-sized
    document (171k spans) that is tens of billions of pure-Python iterations,
    which is what kept annotate jobs in "Registering annotation spans" for
    hours until they hit the execution timeout (2026-07-12 incident). Rows are
    deduplicated (the caller's join can emit one copy per overlapping staged
    span, which also skewed the per-page overlap vote), sorted once, and each
    span visits only its bisected candidate window.
    """
    out: dict[tuple[int, int], dict[str, Any]] = {}
    if not pairs or not layout_rows:
        return out
    rows = sorted(
        {
            (
                int(row["char_start"]),
                int(row["char_end"]),
                int(row["page_number"]),
                float(row["bbox_x0"]),
                float(row["bbox_y0"]),
                float(row["bbox_x1"]),
                float(row["bbox_y1"]),
            )
            for row in layout_rows
        }
    )
    starts = [r[0] for r in rows]
    # Nondecreasing running maximum of char_end: the first index whose value
    # exceeds a span's start bounds the candidate window from the left even
    # when individual row ends are not monotonic.
    max_end_so_far: list[int] = []
    running = rows[0][1]
    for _, row_end, *_ in rows:
        running = max(running, row_end)
        max_end_so_far.append(running)
    for start, end in pairs:
        hi = bisect.bisect_left(starts, end)  # rows with char_start < end
        lo = bisect.bisect_right(max_end_so_far, start, 0, hi)  # first with end > start
        by_page: dict[int, dict[str, Any]] = {}
        for i in range(lo, hi):
            row_start, row_end, page, x0, y0, x1, y1 = rows[i]
            overlap = min(end, row_end) - max(start, row_start)
            if overlap <= 0:
                continue
            page_geom = by_page.get(page)
            if page_geom is None:
                by_page[page] = {
                    "overlap": overlap,
                    "page_number": page,
                    "bbox_x0": x0,
                    "bbox_y0": y0,
                    "bbox_x1": x1,
                    "bbox_y1": y1,
                }
                continue
            page_geom["overlap"] += overlap
            page_geom["bbox_x0"] = min(page_geom["bbox_x0"], x0)
            page_geom["bbox_y0"] = min(page_geom["bbox_y0"], y0)
            page_geom["bbox_x1"] = max(page_geom["bbox_x1"], x1)
            page_geom["bbox_y1"] = max(page_geom["bbox_y1"], y1)
        if by_page:
            best = max(by_page.values(), key=lambda g: (g["overlap"], -g["page_number"]))
            out[(start, end)] = {
                "page_number": best["page_number"],
                "bbox_x0": best["bbox_x0"],
                "bbox_y0": best["bbox_y0"],
                "bbox_x1": best["bbox_x1"],
                "bbox_y1": best["bbox_y1"],
            }
    return out


def replace_document_layout_runs(
    conn: DictConnection,
    document_id: str,
    full_text: str | None,
    page_runs: list[dict[str, Any]] | None,
) -> None:
    """Replace the persisted layout-run index for one document."""
    conn.execute("DELETE FROM document_layout_runs WHERE document_id=%s", (document_id,))
    rows = layout_runs_from_page_runs(full_text or "", page_runs)
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO document_layout_runs
                (document_id, ordinal, page_number, page_width, page_height, char_start, char_end,
                 bbox_x0, bbox_y0, bbox_x1, bbox_y1, text)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id, ordinal) DO UPDATE SET
                page_number = EXCLUDED.page_number,
                page_width = EXCLUDED.page_width,
                page_height = EXCLUDED.page_height,
                char_start = EXCLUDED.char_start,
                char_end = EXCLUDED.char_end,
                bbox_x0 = EXCLUDED.bbox_x0,
                bbox_y0 = EXCLUDED.bbox_y0,
                bbox_x1 = EXCLUDED.bbox_x1,
                bbox_y1 = EXCLUDED.bbox_y1,
                text = EXCLUDED.text
            """,
            [
                (
                    document_id,
                    row["ordinal"],
                    row["page_number"],
                    row["page_width"],
                    row["page_height"],
                    row["char_start"],
                    row["char_end"],
                    row["bbox_x0"],
                    row["bbox_y0"],
                    row["bbox_x1"],
                    row["bbox_y1"],
                    row["text"],
                )
                for row in rows
            ],
        )


def replace_document_pages(
    conn: DictConnection,
    document_id: str,
    page_metrics: list[dict[str, Any]] | None,
) -> None:
    """Replace authoritative per-page geometry for one document."""
    conn.execute("DELETE FROM document_pages WHERE document_id=%s", (document_id,))
    rows = page_rows_from_page_metrics(page_metrics)
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO document_pages
                (document_id, page_number, page_width, page_height, has_text, text_run_count)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id, page_number) DO UPDATE SET
                page_width = EXCLUDED.page_width,
                page_height = EXCLUDED.page_height,
                has_text = EXCLUDED.has_text,
                text_run_count = EXCLUDED.text_run_count
            """,
            [
                (
                    document_id,
                    row["page_number"],
                    row["page_width"],
                    row["page_height"],
                    row["has_text"],
                    row["text_run_count"],
                )
                for row in rows
            ],
        )


def _update_span_geometry_from_layout(
    conn: DictConnection, document_id: str, pairs: list[tuple[int, int]]
) -> None:
    """Populate page/bbox fields for staged spans from persisted layout runs."""
    if not pairs:
        return
    # EXISTS, not JOIN: the join emitted one copy of each layout row per
    # overlapping staged span — for a book that multiplied the rows fed to
    # the geometry pass by orders of magnitude and skewed its overlap vote.
    layout_rows = conn.execute(
        """
        SELECT lr.page_number, lr.char_start, lr.char_end,
               lr.bbox_x0, lr.bbox_y0, lr.bbox_x1, lr.bbox_y1
        FROM document_layout_runs lr
        WHERE lr.document_id=%s
          AND EXISTS (
            SELECT 1 FROM _span_stage st
            WHERE lr.char_start < st.char_end
              AND lr.char_end > st.char_start
          )
        """,
        (document_id,),
    ).fetchall()
    geometry = span_geometry_from_layout_rows(pairs, layout_rows)
    if not geometry:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            UPDATE spans
               SET page_number=%s,
                   bbox_x0=%s,
                   bbox_y0=%s,
                   bbox_x1=%s,
                   bbox_y1=%s
             WHERE document_id=%s AND char_start=%s AND char_end=%s
            """,
            [
                (
                    geom["page_number"],
                    geom["bbox_x0"],
                    geom["bbox_y0"],
                    geom["bbox_x1"],
                    geom["bbox_y1"],
                    document_id,
                    start,
                    end,
                )
                for (start, end), geom in geometry.items()
            ],
        )


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


def _author_id(conn: DictConnection, name: str) -> int:
    """Upsert an author by name and return its id."""
    row = conn.execute(
        "INSERT INTO authors (name) VALUES (%s) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    ).fetchone()
    assert row is not None  # RETURNING guarantees a row
    return int(row["id"])


def _tag_id(conn: DictConnection, tag: str) -> int:
    """Upsert a tag and return its id."""
    row = conn.execute(
        "INSERT INTO tags (tag) VALUES (%s) "
        "ON CONFLICT (tag) DO UPDATE SET tag = EXCLUDED.tag RETURNING id",
        (tag,),
    ).fetchone()
    assert row is not None  # RETURNING guarantees a row
    return int(row["id"])


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

    Returns:
        The id of the newly inserted document.
    """
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


def update_document_metadata(
    conn: DictConnection,
    document_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    actor: str | None = None,
) -> list[str]:
    """Edit a document's title/description/tags, recording one history row per change.

    Only fields actually passed (not ``None``) are considered; a field whose new
    value equals its current value is not treated as a change (no-op, no history
    row). ``tags``, when passed, fully replaces the document's tag set.

    Args:
        conn: Open database connection.
        document_id: The document to edit.
        title: New title, or ``None`` to leave unchanged.
        description: New description, or ``None`` to leave unchanged.
        tags: New complete tag set, or ``None`` to leave unchanged.
        actor: Caller-supplied label recorded on each history row.

    Returns:
        The list of field names actually changed (a subset of
        ``["title", "description", "tags"]``), in that order.

    Raises:
        ValueError: If *document_id* does not name an existing document.
    """
    from doc_ray.domain.ulid import new_ulid_str

    changed: list[str] = []
    with conn.transaction():
        current = conn.execute(
            "SELECT title, description FROM documents WHERE id = %s FOR UPDATE",
            (document_id,),
        ).fetchone()
        if current is None:
            raise ValueError(f"no such document: {document_id}")
        current_tags = None
        if tags is not None:
            current_tags = sorted(
                r["tag"]
                for r in conn.execute(
                    "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
                    "WHERE dt.document_id = %s",
                    (document_id,),
                ).fetchall()
            )

        def _record(field: str, old_value: str | None, new_value: str | None) -> None:
            conn.execute(
                "INSERT INTO document_metadata_history "
                "(id, document_id, field, old_value, new_value, actor) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (new_ulid_str(), document_id, field, old_value, new_value, actor),
            )
            changed.append(field)

        if title is not None and title != current["title"]:
            _record("title", current["title"], title)
            conn.execute("UPDATE documents SET title = %s WHERE id = %s", (title, document_id))

        if description is not None and description != current["description"]:
            _record("description", current["description"], description)
            conn.execute(
                "UPDATE documents SET description = %s WHERE id = %s", (description, document_id)
            )

        if tags is not None:
            new_tags = sorted({t for t in tags if t})
            if new_tags != current_tags:
                _record("tags", ", ".join(current_tags or []), ", ".join(new_tags))
                conn.execute("DELETE FROM document_tags WHERE document_id = %s", (document_id,))
                for tag in new_tags:
                    conn.execute(
                        "INSERT INTO document_tags (document_id, tag_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (document_id, _tag_id(conn, tag)),
                    )
    return changed


def add_document_tags(
    conn: DictConnection,
    document_id: str,
    tags: list[str],
    *,
    actor: str | None = None,
) -> list[str]:
    """Merge tags into a document's current tag set and record one tags change.

    Bulk document actions use this worker-time helper instead of computing a
    replacement tag list in the request. Empty/duplicate tag input is ignored.
    """
    new_tags = sorted({t.strip() for t in tags if t and t.strip()})
    if not new_tags:
        return []
    current_tags = sorted(
        r["tag"]
        for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
            "WHERE dt.document_id = %s",
            (document_id,),
        ).fetchall()
    )
    return update_document_metadata(
        conn,
        document_id,
        tags=sorted(set(current_tags).union(new_tags)),
        actor=actor,
    )


def set_document_lifecycle(
    conn: DictConnection,
    document_id: str,
    lifecycle_state: str,
    *,
    actor: str | None = None,
) -> bool:
    """Set a document's lifecycle state and record the transition in history.

    ``documents.status`` remains the ingestion state; this function manages the
    reversible operator lifecycle added by BL-059. ``deleted`` is a soft delete:
    the document row and blob pointer remain available for restore.
    """
    from doc_ray.domain.ulid import new_ulid_str

    if lifecycle_state not in {"active", "archived", "deleted"}:
        raise ValueError(f"unknown document lifecycle state: {lifecycle_state}")

    with conn.transaction():
        current = conn.execute(
            "SELECT lifecycle_state FROM documents WHERE id = %s FOR UPDATE",
            (document_id,),
        ).fetchone()
        if current is None:
            raise ValueError(f"no such document: {document_id}")
        old_state = current["lifecycle_state"]
        if old_state == lifecycle_state:
            return False

        if lifecycle_state == "active":
            conn.execute(
                "UPDATE documents SET lifecycle_state = 'active', archived_at = NULL, "
                "deleted_at = NULL WHERE id = %s",
                (document_id,),
            )
        elif lifecycle_state == "archived":
            conn.execute(
                "UPDATE documents SET lifecycle_state = 'archived', archived_at = now(), "
                "deleted_at = NULL WHERE id = %s",
                (document_id,),
            )
        else:
            conn.execute(
                "UPDATE documents SET lifecycle_state = 'deleted', deleted_at = now() "
                "WHERE id = %s",
                (document_id,),
            )

        conn.execute(
            "INSERT INTO document_metadata_history "
            "(id, document_id, field, old_value, new_value, actor) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                new_ulid_str(),
                document_id,
                "lifecycle_state",
                old_state,
                lifecycle_state,
                actor,
            ),
        )
    return True
