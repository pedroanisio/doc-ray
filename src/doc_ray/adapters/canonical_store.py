"""Persistence for the canonical registry and remissive index (Stages 0/2/6).

Module-level functions over a live autocommit ``dict_row`` connection, mirroring
:mod:`doc_ray.adapters.persistence`. Fulfils
:class:`doc_ray.ports.canonical_repository.CanonicalRepository`. Seeding is idempotent
per ``wikidata_id``; ``upsert_form`` reinforces an existing form rather than duplicating it.
"""

from __future__ import annotations

from typing import Any

import psycopg

Row = dict[str, Any]
_Conn = psycopg.Connection[Row]


def get_by_wikidata(conn: _Conn, wikidata_id: str) -> Row | None:
    """Return the canonical row for a Wikidata id, or ``None``."""
    return conn.execute("SELECT * FROM canonical WHERE wikidata_id = %s", (wikidata_id,)).fetchone()


def create_canonical(
    conn: _Conn,
    *,
    preferred: str,
    entity_type: str,
    wikidata_id: str | None = None,
    description: str | None = None,
    lang_primary: str | None = None,
) -> int:
    """Insert a canonical entity (idempotent on ``wikidata_id``) and return its id."""
    if wikidata_id is not None:
        existing = get_by_wikidata(conn, wikidata_id)
        if existing is not None:
            return int(existing["id"])
    try:
        row = conn.execute(
            "INSERT INTO canonical "
            "  (wikidata_id, preferred, entity_type, description, lang_primary) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (wikidata_id, preferred, entity_type, description, lang_primary),
        ).fetchone()
    except psycopg.errors.UniqueViolation:
        # Concurrent seed of the same wikidata_id — reuse the row that won.
        existing = get_by_wikidata(conn, wikidata_id) if wikidata_id else None
        if existing is not None:
            return int(existing["id"])
        raise
    assert row is not None
    return int(row["id"])


def upsert_form(
    conn: _Conn,
    *,
    form_norm: str,
    form_surface: str,
    canonical_id: int,
    form_type: str,
    lang: str = "",
    confidence: float = 1.0,
    occurrences: int = 0,
    source_type: str | None = None,
) -> None:
    """Insert or reinforce one ``form_index`` row (the flywheel write-back).

    On conflict the occurrence count accumulates, the confidence takes the max, and the
    source-type set unions — so re-seeing a form strengthens its entry, never duplicates it.
    """
    source_types = [source_type] if source_type else []
    conn.execute(
        "INSERT INTO form_index "
        "  (form_norm, form_surface, canonical_id, lang, form_type, confidence, occurrences, "
        "   source_types) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (form_norm, canonical_id, lang) DO UPDATE SET "
        "  occurrences = form_index.occurrences + EXCLUDED.occurrences, "
        "  confidence  = GREATEST(form_index.confidence, EXCLUDED.confidence), "
        "  source_types = ARRAY(SELECT DISTINCT unnest("
        "                       form_index.source_types || EXCLUDED.source_types))",
        (
            form_norm,
            form_surface,
            canonical_id,
            lang,
            form_type,
            confidence,
            occurrences,
            source_types,
        ),
    )


def lookup_form(conn: _Conn, form_norm: str) -> list[Row]:
    """Return remissive-index rows (joined to their canonical) for a normalized form."""
    return conn.execute(
        "SELECT f.canonical_id, f.form_surface, f.form_type, f.lang, f.confidence, "
        "       c.preferred, c.entity_type, c.wikidata_id "
        "FROM form_index f JOIN canonical c ON c.id = f.canonical_id "
        "WHERE f.form_norm = %s "
        "ORDER BY f.confidence DESC, f.occurrences DESC",
        (form_norm,),
    ).fetchall()


def forms_for(conn: _Conn, canonical_id: int) -> list[Row]:
    """Return every surface form registered for a canonical entity (the índice remissivo)."""
    return conn.execute(
        "SELECT form_surface, form_norm, lang, form_type, confidence, occurrences, source_types "
        "FROM form_index WHERE canonical_id = %s ORDER BY form_type, form_surface",
        (canonical_id,),
    ).fetchall()


def mentions_for_document(conn: _Conn, document_id: str) -> list[Row]:
    """Return a document's entity mentions with their surface text and span offsets."""
    return conn.execute(
        "SELECT em.id AS mention_id, em.entity_type, sp.char_start, sp.char_end, "
        "       substring(c.full_text FROM sp.char_start + 1 FOR sp.char_end - sp.char_start) "
        "         AS surface "
        "FROM entity_mention em "
        "JOIN annotations a       ON a.id = em.annotation_id "
        "JOIN spans sp            ON sp.id = em.span_id "
        "JOIN document_content c  ON c.document_id = a.document_id "
        "WHERE a.document_id = %s ORDER BY sp.char_start",
        (document_id,),
    ).fetchall()


def link_mention(
    conn: _Conn, mention_id: str, canonical_id: int, *, confidence: float = 1.0
) -> None:
    """Record the mention -> canonical resolution edge (reuses entity_link, scheme='canonical')."""
    conn.execute(
        "INSERT INTO entity_link (mention_id, scheme, kb_id, confidence) "
        "VALUES (%s, 'canonical', %s, %s) "
        "ON CONFLICT (mention_id, scheme) DO UPDATE SET "
        "  kb_id = EXCLUDED.kb_id, confidence = EXCLUDED.confidence",
        (mention_id, str(canonical_id), confidence),
    )


def canonical_for_mention(conn: _Conn, mention_id: str) -> int | None:
    """Return the canonical id a mention resolves to, or ``None`` if unresolved."""
    row = conn.execute(
        "SELECT kb_id FROM entity_link WHERE mention_id = %s AND scheme = 'canonical'",
        (mention_id,),
    ).fetchone()
    return int(row["kb_id"]) if row is not None else None
