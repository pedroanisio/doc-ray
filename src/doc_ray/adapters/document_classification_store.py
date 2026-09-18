"""Persistence for document genre classification (issue #6).

Bridges the heuristic classifier (``doc_ray.application.genre_classification``) and
the human confirm path to the ``document_classification`` table (migration 127),
one row per document (PK on ``document_id``).

The load-bearing invariant (PALS's Law): a machine re-run **never** overwrites a
human decision. :func:`upsert_machine_classification` only touches a row while it
is still ``pending``; a ``confirmed`` or ``rejected`` row (a human's call) is left
untouched. Human confirmation (:func:`confirm_classification_async`) overrides
unconditionally and stamps ``method='human'`` + the reviewer.

All reads are a single PK lookup — O(1), no aggregate on a read path (D-024).
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.application.genre_classification import GenreClassification
from doc_ray.domain.genre_policy import Genre

from .persistence import DictConnection

#: The GraphQL API runs on an async psycopg connection with ``dict_row``.
AsyncConnection = psycopg.AsyncConnection[dict[str, Any]]

_SELECT = """
    SELECT document_id, genre, subtype, confidence, method, status,
           reviewer, rationale, created_at, updated_at
    FROM document_classification
    WHERE document_id = %s
"""

# Machine upsert: write a fresh guess, or refresh an existing PENDING one. The
# WHERE on the conflict clause protects every human decision — a confirmed or
# rejected row is never clobbered by a re-run (the load-bearing invariant).
_UPSERT_MACHINE = """
    INSERT INTO document_classification
        (document_id, genre, subtype, confidence, method, status, rationale, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (document_id) DO UPDATE
    SET genre = EXCLUDED.genre,
        subtype = EXCLUDED.subtype,
        confidence = EXCLUDED.confidence,
        method = EXCLUDED.method,
        status = EXCLUDED.status,
        rationale = EXCLUDED.rationale,
        reviewer = NULL,
        updated_at = now()
    WHERE document_classification.status = 'pending'
    RETURNING status
"""

# Human confirmation overrides unconditionally: it is the verification decision.
_CONFIRM = """
    INSERT INTO document_classification
        (document_id, genre, subtype, confidence, method, status, reviewer, rationale, updated_at)
    VALUES (%s, %s, %s, NULL, 'human', 'confirmed', %s, %s, now())
    ON CONFLICT (document_id) DO UPDATE
    SET genre = EXCLUDED.genre,
        subtype = EXCLUDED.subtype,
        confidence = NULL,
        method = 'human',
        status = 'confirmed',
        reviewer = EXCLUDED.reviewer,
        rationale = EXCLUDED.rationale,
        updated_at = now()
    RETURNING document_id
"""


def upsert_machine_classification(
    conn: DictConnection, document_id: str, result: GenreClassification
) -> bool:
    """Write a machine classification, protecting any human decision.

    Returns:
        ``True`` if a row was written or refreshed; ``False`` if an existing
        ``confirmed``/``rejected`` row was left untouched (a human already decided).
    """
    row = conn.execute(
        _UPSERT_MACHINE,
        (
            document_id,
            result.genre.value,
            result.subtype,
            result.confidence,
            result.method,
            result.status,
            result.rationale,
        ),
    ).fetchone()
    return row is not None


def get_classification(conn: DictConnection, document_id: str) -> dict[str, Any] | None:
    """Return the document's classification row, or ``None`` if unclassified (sync)."""
    return conn.execute(_SELECT, (document_id,)).fetchone()


async def get_classification_async(
    conn: AsyncConnection, document_id: str
) -> dict[str, Any] | None:
    """Return the document's classification row, or ``None`` if unclassified (async)."""
    cur = await conn.execute(_SELECT, (document_id,))
    return await cur.fetchone()


async def confirm_classification_async(
    conn: AsyncConnection,
    document_id: str,
    *,
    genre: Genre,
    subtype: str | None,
    reviewer: str | None,
    rationale: str | None,
) -> None:
    """Record a human genre confirmation (``method='human'``, ``status='confirmed'``).

    Overrides any prior machine or human value — this is the verification decision.
    """
    await conn.execute(_CONFIRM, (document_id, genre.value, subtype, reviewer, rationale))
