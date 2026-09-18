"""Batch-side refresh for the hyper-context lens rollups (issue #10, FR-4).

Aggregates the projection engine projections of a lens into two derived rollups (migration 133):

* ``lens_entity_rollup`` — per entity: mention/document volume, the observed type
  profile + its consistency (reusing :func:`entity_profiles.dominant_type_share`), and
  the earliest sentence ordinal for citation.
* ``lens_entity_relation`` — undirected sentence-level co-occurrence pairs (stored once,
  ``from < to``) with an NPMI strength (:func:`domain.lens_rollups.npmi`).

Runs on the batch connection (``docray_batch``/owner, BYPASSRLS). It strictly FOLLOWS
the projection engine's projection refresh — it reads ``entity_projection`` — and is idempotent:
each refresh replaces the lens's rows (DELETE + INSERT), so a full rebuild is
byte-identical to an incremental one. No live aggregate ever runs on a read path (D-024);
this is where the aggregation happens, at the pipeline boundary.

⚠ PALS's LAW: a co-occurrence strength is a structural measure, not a claim of a real
relationship; consumers read it with the ``refreshed_at`` staleness stamp.
"""

from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations
from typing import Any

import psycopg

from doc_ray.domain.entity_profiles import dominant_type_share
from doc_ray.domain.lens_rollups import npmi

_Conn = psycopg.Connection[dict[str, Any]]

# Projected mentions of a lens, with entity type + document (for volume + type profile).
_MENTIONS_SQL = """
    SELECT ep.canonical_id, ep.scope, em.entity_type, ep.document_id
    FROM entity_projection ep
    JOIN entity_mention em ON em.id = ep.mention_id
    WHERE ep.lens_id = %s AND ep.canonical_id IS NOT NULL
"""

# The same mentions mapped to their containing sentence (span containment within the
# sentence's span, same document) — the sentence-level co-occurrence universe (D-1) and
# the source of first_seen_ordinal. A mention with no containing sentence is simply
# absent from co-occurrence (it still counts in the volume rollup above).
_SENTENCE_MAP_SQL = """
    SELECT ep.canonical_id, s.id AS sentence_id, s.ordinal
    FROM entity_projection ep
    JOIN entity_mention em ON em.id = ep.mention_id
    JOIN spans msp ON msp.id = em.span_id
    JOIN sentences s ON s.document_id = ep.document_id
    JOIN spans ssp ON ssp.id = s.span_id
     AND ssp.char_start <= msp.char_start AND ssp.char_end >= msp.char_end
    WHERE ep.lens_id = %s AND ep.canonical_id IS NOT NULL
"""


def _build_entity_rollup(mention_rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Fold projected mentions into per-canonical volume + type profile (pure aggregation)."""
    types: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    docs: dict[int, set[str]] = defaultdict(set)
    total: dict[int, int] = defaultdict(int)
    scope: dict[int, str] = {}
    for r in mention_rows:
        cid = int(r["canonical_id"])
        types[cid][r["entity_type"]] += 1
        docs[cid].add(str(r["document_id"]))
        total[cid] += 1
        scope.setdefault(cid, r["scope"])
    return {
        cid: {
            "mention_count": total[cid],
            "document_count": len(docs[cid]),
            "type_profile": dict(types[cid]),
            "type_consistency": dominant_type_share(types[cid]),
            "scope": scope[cid],
        }
        for cid in total
    }


def _build_relations(
    sentence_rows: list[dict[str, Any]],
) -> tuple[dict[int, int], dict[tuple[int, int], int], int]:
    """Return (per-entity sentence df, per-pair co-occurrence, total sentences).

    Sentence-level: group entities by sentence, emit each unordered pair once
    (``a < b``). ``df`` is how many sentences contain an entity; ``total`` is the number
    of co-occurrence-bearing sentences — the NPMI universe.
    """
    per_sentence: dict[str, set[int]] = defaultdict(set)
    for r in sentence_rows:
        per_sentence[str(r["sentence_id"])].add(int(r["canonical_id"]))
    df: dict[int, int] = defaultdict(int)
    cooccur: dict[tuple[int, int], int] = defaultdict(int)
    for entities in per_sentence.values():
        for cid in entities:
            df[cid] += 1
        for a, b in combinations(sorted(entities), 2):
            cooccur[(a, b)] += 1
    return df, cooccur, len(per_sentence)


def _first_seen(sentence_rows: list[dict[str, Any]]) -> dict[int, int]:
    out: dict[int, int] = {}
    for r in sentence_rows:
        cid = int(r["canonical_id"])
        ordv = int(r["ordinal"])
        if cid not in out or ordv < out[cid]:
            out[cid] = ordv
    return out


def refresh_lens_rollup(conn: _Conn, lens_id: str) -> dict[str, Any]:
    """(Re)build both rollups for one lens from its projections; return a summary.

    Idempotent: replaces the lens's rows. Scope lenses only — the global lens's
    corpus-wide landscape would aggregate across tenants and is served by the
    canonical/entity_frequency surfaces (mirrors the projection engine stats rule).
    """
    mention_rows = conn.execute(_MENTIONS_SQL, (lens_id,)).fetchall()
    sentence_rows = conn.execute(_SENTENCE_MAP_SQL, (lens_id,)).fetchall()

    rollup = _build_entity_rollup(mention_rows)
    first_seen = _first_seen(sentence_rows)
    df, cooccur, total_sentences = _build_relations(sentence_rows)

    conn.execute("DELETE FROM lens_entity_rollup WHERE lens_id = %s", (lens_id,))
    conn.execute("DELETE FROM lens_entity_relation WHERE lens_id = %s", (lens_id,))

    if rollup:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO lens_entity_rollup (lens_id, canonical_id, scope, mention_count, "
                "document_count, type_profile, type_consistency, first_seen_ordinal) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        lens_id,
                        cid,
                        v["scope"],
                        v["mention_count"],
                        v["document_count"],
                        json.dumps(v["type_profile"]),
                        v["type_consistency"],
                        first_seen.get(cid),
                    )
                    for cid, v in rollup.items()
                ],
            )
    if cooccur:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO lens_entity_relation (lens_id, from_canonical_id, to_canonical_id, "
                "cooccurrence_count, strength) VALUES (%s, %s, %s, %s, %s)",
                [
                    (lens_id, a, b, n, npmi(n, df[a], df[b], total_sentences))
                    for (a, b), n in cooccur.items()
                ],
            )
    return {
        "lens_id": lens_id,
        "entities": len(rollup),
        "relations": len(cooccur),
        "cooccurrence_sentences": total_sentences,
    }
