"""Worker-side persistence for the projection engine (issue #8).

The pure resolver (:mod:`doc_ray.domain.projection`) decides *what* a mention
projects to; this adapter supplies its inputs from the substrate and persists its
outputs. It runs on the batch connection (``docray_batch``, BYPASSRLS — migration
106), so it reads the whole document regardless of RLS and writes the derived fact
tables the API may only read (migration 129, FR-1).

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — everything written here is a PROPOSAL.
Machine projections land ``status='proposed'``; only a human confirmation
(``entity_projection_review``) promotes one to ``confirmed`` (invariant 6).

Nothing in this module mutates the substrate (``entity_mention``, ``spans``,
``canonical`` global rows): it only appends local-only ``canonical`` rows for
work-local identities (the "local-only entity" the 021 schema anticipated) and
writes the derived projection/rollup tables (invariants 1 & 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from doc_ray.domain.genre_policy import Genre
from doc_ray.domain.projection import GlobalLink, Mention, Projection

_Conn = psycopg.Connection[dict[str, Any]]


@dataclass(frozen=True)
class LensRow:
    """A projecting lens a document is resolved through."""

    lens_id: str
    name: str
    is_global: bool


@dataclass(frozen=True)
class ProjectionInputs:
    """A document's substrate facts, ready for :func:`doc_ray.domain.projection.project`."""

    mentions: list[Mention]
    #: mention_id -> current global resolution (entity_link → canonical).
    global_index: dict[str, GlobalLink]
    #: mention_id -> stable span key (char_start, char_end), for review re-application.
    spans: dict[str, tuple[int, int]]


_INPUTS_SQL = """
    SELECT em.id AS mention_id, em.entity_type, em.surface,
           sp.char_start, sp.char_end,
           el.kb_id AS global_kb, el.confidence AS global_conf
    FROM entity_mention em
    JOIN annotations a ON a.id = em.annotation_id
    JOIN spans sp ON sp.id = em.span_id
    LEFT JOIN entity_link el ON el.mention_id = em.id AND el.scheme = 'canonical'
    WHERE a.document_id = %s
    ORDER BY sp.char_start, em.id
"""


def _clamp_confidence(value: float | int | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def load_projection_inputs(conn: _Conn, document_id: str) -> ProjectionInputs:
    """Load a document's mentions, their global links, and stable span keys."""
    rows = conn.execute(_INPUTS_SQL, (document_id,)).fetchall()
    mentions: list[Mention] = []
    global_index: dict[str, GlobalLink] = {}
    spans: dict[str, tuple[int, int]] = {}
    for row in rows:
        mid = str(row["mention_id"])
        mentions.append(
            Mention(mention_id=mid, entity_type=row["entity_type"], surface=row["surface"])
        )
        spans[mid] = (int(row["char_start"]), int(row["char_end"]))
        if row["global_kb"] is not None:
            global_index[mid] = GlobalLink(
                canonical_id=int(row["global_kb"]),
                confidence=_clamp_confidence(row["global_conf"]),
            )
    return ProjectionInputs(mentions=mentions, global_index=global_index, spans=spans)


def get_document_genre(conn: _Conn, document_id: str) -> Genre:
    """Return a document's genre for routing, conservatively (PALS's Law).

    Uses the stored :mod:`document_classification` label unless it was
    ``rejected`` — a rejected genre must not drive local resolution — in which case
    it falls back to :attr:`Genre.UNKNOWN`, which routes everything GLOBAL. An
    unrecognized stored value also degrades to ``UNKNOWN`` rather than raising.
    """
    row = conn.execute(
        "SELECT genre, status FROM document_classification WHERE document_id = %s",
        (document_id,),
    ).fetchone()
    if row is None or row["status"] == "rejected":
        return Genre.UNKNOWN
    try:
        return Genre(row["genre"])
    except ValueError:
        return Genre.UNKNOWN


def projecting_lenses_for_document(
    conn: _Conn, document_id: str, *, max_lenses: int
) -> tuple[list[LensRow], int]:
    """Return the projecting lenses for a document and the count dropped by the bound.

    Always includes the reserved ``global`` lens (it always projects — the fallback
    every mention resolves against). Adds the scope lenses the document is a member of
    that carry ``projects = true``. Deterministic order (``global`` first, then by
    name, id) so a bound truncates the *same* lenses every run, and the caller can log
    what it dropped rather than silently losing a lens (FR-2).
    """
    rows = conn.execute(
        """
        SELECT c.id, c.name, (c.name = 'global') AS is_global
        FROM document_collections c
        WHERE c.name = 'global'
           OR (c.is_scope AND c.projects
               AND EXISTS (SELECT 1 FROM document_collection_members m
                           WHERE m.collection_id = c.id AND m.document_id = %s))
        ORDER BY (c.name = 'global') DESC, c.name, c.id
        """,
        (document_id,),
    ).fetchall()
    lenses = [
        LensRow(lens_id=str(r["id"]), name=r["name"], is_global=bool(r["is_global"])) for r in rows
    ]
    dropped = max(0, len(lenses) - max_lenses)
    return lenses[:max_lenses], dropped


def ensure_local_candidates(
    conn: _Conn, lens_id: str, keys: list[tuple[str, str, str]]
) -> dict[tuple[str, str], int]:
    """Get-or-mint the lens-local identity for each ``(surface_norm, type, display)`` key.

    Returns ``{(surface_norm, entity_type): canonical_id}`` for use as the
    ``local_candidates`` map. Minting reuses the stable ``lens_local_entity`` registry
    so a rebuild is reproducible (invariant 3): the same key always resolves to the
    same ``canonical`` row across refreshes. New local identities are ``canonical``
    rows with ``wikidata_id = NULL`` (a work-local entity), never touching a global row.
    """
    out: dict[tuple[str, str], int] = {}
    for surface_norm, entity_type, display in keys:
        existing = conn.execute(
            "SELECT canonical_id FROM lens_local_entity "
            "WHERE lens_id = %s AND surface_norm = %s AND entity_type = %s",
            (lens_id, surface_norm, entity_type),
        ).fetchone()
        if existing is not None:
            out[(surface_norm, entity_type)] = int(existing["canonical_id"])
            continue
        minted = conn.execute(
            "INSERT INTO canonical (preferred, entity_type) VALUES (%s, %s) RETURNING id",
            (display, entity_type),
        ).fetchone()
        assert minted is not None  # INSERT ... RETURNING always yields a row
        cid = int(minted["id"])
        # ON CONFLICT tolerates a concurrent minter: keep whoever won, drop our extra row.
        won = conn.execute(
            "INSERT INTO lens_local_entity (lens_id, surface_norm, entity_type, canonical_id) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (lens_id, surface_norm, entity_type) DO UPDATE SET canonical_id = "
            "lens_local_entity.canonical_id RETURNING canonical_id",
            (lens_id, surface_norm, entity_type, cid),
        ).fetchone()
        assert won is not None  # INSERT ... RETURNING always yields a row
        winner = int(won["canonical_id"])
        if winner != cid:
            conn.execute("DELETE FROM canonical WHERE id = %s", (cid,))
        out[(surface_norm, entity_type)] = winner
    return out


def replace_projections(
    conn: _Conn, document_id: str, lens_id: str, projections: list[Projection]
) -> int:
    """Replace a document's projection rows for one lens (delete-then-insert).

    Delete-then-insert (rather than upsert) so a mention that stopped existing — e.g.
    after a re-annotation produced fewer mentions — leaves no stale projection behind,
    and the rebuild is deterministic. ``document_id`` is derived here (the deliberate
    denormalization, migration 129 FR-1); the caller passes the mention's document.
    """
    conn.execute(
        "DELETE FROM entity_projection WHERE document_id = %s AND lens_id = %s",
        (document_id, lens_id),
    )
    if not projections:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO entity_projection "
            "(mention_id, lens_id, document_id, canonical_id, scope, confidence, method, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    p.mention_id,
                    lens_id,
                    document_id,
                    p.canonical_id,
                    p.scope,
                    p.confidence,
                    p.method,
                    p.status,
                )
                for p in projections
            ],
        )
    return len(projections)


def apply_reviews(conn: _Conn, document_id: str, lens_id: str) -> int:
    """Apply the latest human review per span onto the freshly written projections.

    Re-applied on every refresh so a confirm/reject survives re-annotation (the review
    is keyed by the stable span, not the ephemeral mention_id — the NER-correction log's pattern).
    Returns the number of projection rows whose status changed from the default.
    """
    result = conn.execute(
        """
        UPDATE entity_projection ep
        SET status = latest.decision
        FROM entity_mention em
        JOIN annotations a ON a.id = em.annotation_id
        JOIN spans sp ON sp.id = em.span_id
        JOIN LATERAL (
            SELECT epr.decision
            FROM entity_projection_review epr
            WHERE epr.document_id = %(doc)s AND epr.lens_id = %(lens)s
              AND epr.char_start = sp.char_start AND epr.char_end = sp.char_end
            ORDER BY epr.created_at DESC
            LIMIT 1
        ) latest ON true
        WHERE ep.mention_id = em.id AND ep.lens_id = %(lens)s
          AND a.document_id = %(doc)s
          AND ep.status IS DISTINCT FROM latest.decision
        """,
        {"doc": document_id, "lens": lens_id},
    )
    return result.rowcount
