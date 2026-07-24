"""Persistence for NER corrections — record, load, and re-apply to the corpus.

Bridges the pure domain (``doc_ray.domain.ner_corrections``) to Postgres:

* :func:`record_correction` appends a human decision to
  ``entity_mention_correction`` (migration 115), keyed by a stable span
  identity so it outlives the ephemeral ``entity_mention`` rows.
* :func:`load_corrections` reads the correction log back as domain objects.
* :func:`apply_corrections` is the reconcile hook: it reads the current ML
  mentions for an annotation, runs the deterministic domain reconcile, and
  rewrites the annotation's mentions to match — so a fix survives every
  ``reannotate``. Idempotent by construction (delete-then-rebuild the set).
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.domain.ner_corrections import Action, Correction, Mention, reconcile
from doc_ray.domain.ulid import new_ulid_str

from . import persistence as db
from .persistence import DictConnection

#: The GraphQL API runs on an async psycopg connection with ``dict_row``.
AsyncConnection = psycopg.AsyncConnection[dict[str, Any]]

_LOAD_CORRECTIONS = """
    SELECT action, char_start, char_end, entity_type, surface,
           new_char_start, new_char_end
    FROM entity_mention_correction
    WHERE document_id = %s
    ORDER BY created_at, id
"""

_CURRENT_MENTIONS = """
    SELECT sp.char_start, sp.char_end, em.entity_type, em.surface
    FROM entity_mention em
    JOIN spans sp ON sp.id = em.span_id
    WHERE em.annotation_id = %s
    ORDER BY sp.char_start, sp.char_end
"""


def record_correction(
    conn: DictConnection,
    document_id: str,
    correction: Correction,
    *,
    reviewer: str | None = None,
    rationale: str | None = None,
    original_type: str | None = None,
) -> str:
    """Append a validated correction to the log; return its id."""
    correction.validate()
    row = conn.execute(
        """
        INSERT INTO entity_mention_correction
            (document_id, action, char_start, char_end, entity_type, surface,
             new_char_start, new_char_end, original_type, rationale, reviewer)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            document_id,
            correction.action.value,
            correction.char_start,
            correction.char_end,
            correction.entity_type,
            correction.surface,
            correction.new_char_start,
            correction.new_char_end,
            original_type,
            rationale,
            reviewer,
        ),
    ).fetchone()
    assert row is not None  # INSERT ... RETURNING always yields a row
    return str(row["id"])


def load_corrections(conn: DictConnection, document_id: str) -> list[Correction]:
    """Read the document's correction log, oldest first (replay order)."""
    rows = conn.execute(_LOAD_CORRECTIONS, (document_id,)).fetchall()
    return [
        Correction(
            action=Action(r["action"]),
            char_start=r["char_start"],
            char_end=r["char_end"],
            entity_type=r["entity_type"],
            surface=r["surface"],
            new_char_start=r["new_char_start"],
            new_char_end=r["new_char_end"],
        )
        for r in rows
    ]


def _current_mentions(conn: DictConnection, annotation_id: str) -> list[Mention]:
    rows = conn.execute(_CURRENT_MENTIONS, (annotation_id,)).fetchall()
    return [
        Mention(
            char_start=r["char_start"],
            char_end=r["char_end"],
            entity_type=r["entity_type"],
            surface=r["surface"] or "",
        )
        for r in rows
    ]


def apply_corrections(conn: DictConnection, document_id: str, annotation_id: str) -> list[Mention]:
    """Re-apply the correction log onto one annotation's ML mentions.

    Reads the annotation's mentions, reconciles them with the document's
    corrections, and rewrites the annotation's ``entity_mention`` rows to the
    reconciled set (registering any span the reconcile introduced). Idempotent:
    running it again with the same inputs is a no-op. Call it at the end of the
    annotate/reannotate pipeline so human fixes are re-applied on every run.
    Returns the reconciled mentions.
    """
    mentions = _current_mentions(conn, annotation_id)
    corrections = load_corrections(conn, document_id)
    corrected = reconcile(mentions, corrections)
    if corrected == mentions:
        return corrected  # nothing to change — the common no-correction case

    span_ids = db.ensure_spans(conn, document_id, [(m.char_start, m.char_end) for m in corrected])
    conn.execute("DELETE FROM entity_mention WHERE annotation_id = %s", (annotation_id,))
    for m in corrected:
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                new_ulid_str(),
                annotation_id,
                span_ids[(m.char_start, m.char_end)],
                m.entity_type,
                m.surface,
            ),
        )
    return corrected


def apply_corrections_to_document(conn: DictConnection, document_id: str) -> bool:
    """Re-apply corrections onto whichever annotation holds the doc's mentions.

    The pipeline hook (and reannotate) call this — they need not know the
    annotation id. Resolves every annotation that currently owns NER mentions
    for the document and reconciles each. Returns True if any mention changed.
    Safe to call unconditionally: a no-op when the document has no corrections.
    """
    rows = conn.execute(
        "SELECT DISTINCT em.annotation_id AS aid FROM entity_mention em "
        "JOIN annotations an ON an.id = em.annotation_id WHERE an.document_id = %s",
        (document_id,),
    ).fetchall()
    corrections = load_corrections(conn, document_id)
    if not corrections:
        return False
    changed = False
    for r in rows:
        before = _current_mentions(conn, r["aid"])
        after = apply_corrections(conn, document_id, r["aid"])
        changed = changed or after != before
    return changed


# --- Async variants for the GraphQL API (async psycopg) --------------------
#
# The API records a correction and re-applies it to the document's live
# mentions immediately, so a curator sees the fix without waiting for the next
# annotate pass. It reuses the same pure reconcile; only the I/O is async. The
# sync pipeline hook remains the authority that survives full re-annotation.


async def record_correction_async(
    conn: AsyncConnection,
    document_id: str,
    correction: Correction,
    *,
    reviewer: str | None = None,
    rationale: str | None = None,
    original_type: str | None = None,
) -> str:
    """Async :func:`record_correction` for the GraphQL API."""
    correction.validate()
    cur = await conn.execute(
        """
        INSERT INTO entity_mention_correction
            (document_id, action, char_start, char_end, entity_type, surface,
             new_char_start, new_char_end, original_type, rationale, reviewer)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            document_id,
            correction.action.value,
            correction.char_start,
            correction.char_end,
            correction.entity_type,
            correction.surface,
            correction.new_char_start,
            correction.new_char_end,
            original_type,
            rationale,
            reviewer,
        ),
    )
    row = await cur.fetchone()
    assert row is not None  # INSERT ... RETURNING always yields a row
    return str(row["id"])


_LIST_CORRECTION_ROWS = """
    SELECT id, document_id, action, char_start, char_end, entity_type, surface,
           new_char_start, new_char_end, original_type, rationale, reviewer, created_at
    FROM entity_mention_correction
    WHERE document_id = %s
    ORDER BY created_at, id
"""


async def list_correction_rows_async(
    conn: AsyncConnection, document_id: str
) -> list[dict[str, Any]]:
    """Return the document's correction log as full rows (the API audit view)."""
    cur = await conn.execute(_LIST_CORRECTION_ROWS, (document_id,))
    return await cur.fetchall()


# NOTE: there is deliberately no async apply. ``entity_mention``/``spans`` are
# owned by the annotate pipeline (docray_batch); docray_api/docray_client hold
# only SELECT on them (migration 010/032). The API therefore records the
# correction and enqueues an ``apply_corrections`` job — the worker
# (docray_batch) re-applies via the sync :func:`apply_corrections_to_document`.
