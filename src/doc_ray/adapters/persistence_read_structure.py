"""Read adapters for document structure and reconciled entities (BL-146).

Two read paths that exist to make the rest of the corpus reachable:

* :func:`document_outline` maps a document into a handful of addressable ordinal
  ranges, so ``read_document_sentences(from_ordinal=…)`` stops being a guess.
* :func:`entity_profiles` folds raw NER frequency rows into one row per entity,
  carrying the measured type disagreement instead of hiding it.

Both obey D-024: neither aggregates a fact table on the request path. The outline
resolves at most ``max_entries`` heading offsets through indexed single-row
lookups, and the profiles are grouped in :mod:`doc_ray.domain.entity_profiles`
from rows the existing frequency read path already serves (the corpus-wide branch
of which reads the ``entity_frequency_stats`` rollup).
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from doc_ray.adapters import persistence_read as read
from doc_ray.domain import document_outline as outline_domain
from doc_ray.domain.entity_profiles import reconcile

_AConn = AsyncConnection[Any]
Row = dict[str, Any]

#: Heading lines pulled before level-trimming. Generous enough that a deeply
#: structured book still offers a choice of levels, bounded so a pathological
#: document cannot stream its whole body back as "headings".
_HEADING_SCAN_CAP = 2000

#: Raw frequency rows read per requested profile. Grouping merges rows (Arrakis
#: alone occupies four), so the raw window must be wider than the answer.
_RAW_ROWS_PER_PROFILE = 6

#: Characters of a sentence used to label a fallback bucket.
_LABEL_CHARS = 90

#: Every ATX heading line, with its absolute offset into the document text. The
#: window sum walks the line array to recover offsets ``string_to_array`` drops;
#: ``document_content`` is a per-document blob, not a fact table, so this is
#: O(one document) and D-024-clean.
_HEADING_LINES_SQL = """
SELECT x.line AS line, x.char_start AS char_start
FROM (
    SELECT l.line AS line,
           (sum(length(l.line) + 1) OVER (ORDER BY l.ord))::int
               - length(l.line) - 1 AS char_start
    FROM document_content dc,
         LATERAL unnest(string_to_array(dc.full_text, E'\\n')) WITH ORDINALITY AS l(line, ord)
    WHERE dc.document_id = %(doc)s
) x
WHERE x.line ~ '^#{1,6}[ \\t]+[^ \\t]'
ORDER BY x.char_start
LIMIT %(cap)s
"""

#: Resolve each heading offset onto the sentence containing it (one indexed
#: lookup per heading — bounded, never a scan).
_RESOLVE_OFFSETS_SQL = """
SELECT h.idx AS idx, s.ordinal AS ordinal, s.page_number AS page_number
FROM unnest(%(starts)s::int[]) WITH ORDINALITY AS h(cs, idx)
LEFT JOIN LATERAL (
    SELECT s2.ordinal AS ordinal, sp2.page_number AS page_number
    FROM sentences s2 JOIN spans sp2 ON sp2.id = s2.span_id
    WHERE s2.document_id = %(doc)s AND sp2.char_start <= h.cs
    ORDER BY sp2.char_start DESC
    LIMIT 1
) s ON TRUE
ORDER BY h.idx
"""

#: The sentences that open each fallback bucket, fetched by exact ordinal.
_BUCKET_SENTENCES_SQL = """
SELECT s.ordinal AS ordinal, left(s.text, %(chars)s) AS label,
       sp.char_start AS char_start, sp.char_end AS char_end, sp.page_number AS page_number
FROM sentences s JOIN spans sp ON sp.id = s.span_id
WHERE s.document_id = %(doc)s AND s.ordinal = ANY(%(ordinals)s::int[])
ORDER BY s.ordinal
"""

#: Canonical registry rows for a batch of normalized forms (small registries).
_CANONICAL_SQL = """
SELECT fi.form_norm AS form_norm, c.id AS canonical_id, c.preferred AS preferred,
       c.wikidata_id AS wikidata_id
FROM form_index fi JOIN canonical c ON c.id = fi.canonical_id
WHERE fi.form_norm = ANY(%(keys)s::text[])
ORDER BY fi.form_norm, fi.confidence DESC, c.id
"""


async def _rows(conn: _AConn, sql: str, params: dict[str, Any]) -> list[Row]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())


def _empty_outline(note: str) -> Row:
    return {
        "source": "empty",
        "total_sentences": 0,
        "entry_count": 0,
        "note": note,
        "entries": [],
    }


async def document_outline(conn: _AConn, document_id: str, *, max_entries: int = 50) -> Row:
    """Return a navigable map of one document, in ordinals the readers accept.

    Prefers the headings every extractor normalizes into the stored text; falls
    back to an even split when detection found fewer than two — measured across
    the live corpus, the PDF font-size heuristic finds none at all in some
    novels, and an empty map would fail exactly the documents this exists for.

    Args:
        conn: Async database connection (RLS decides visibility).
        document_id: UUID of the document to map.
        max_entries: Upper bound on returned entries.

    Returns:
        ``{source, total_sentences, entry_count, note, entries}`` where ``source``
        is ``headings`` (real structure), ``uniform`` (an even grid, explicitly
        not a table of contents), or ``empty``. Never raises for an unknown or
        unreadable document — it returns an empty outline.
    """
    max_entries = max(1, min(max_entries, 200))
    meta = await _rows(
        conn,
        "SELECT d.char_count AS char_count, COALESCE(ds.n_sentences, 0)::int AS n_sentences "
        "FROM documents d LEFT JOIN document_stats ds ON ds.document_id = d.id "
        "WHERE d.id = %(doc)s",
        {"doc": document_id},
    )
    if not meta:
        return _empty_outline("No such document, or it is not readable with this token.")
    total = int(meta[0]["n_sentences"] or 0)
    total_chars = int(meta[0]["char_count"] or 0) or None
    if total <= 0:
        # document_stats is refreshed by the annotation job handlers, so a
        # freshly-extracted document has a zero rollup while its sentences
        # already exist. Fall back to the highest ordinal — one index-only
        # lookup, no aggregate — rather than claim the document is empty.
        last = await _rows(
            conn,
            "SELECT s.ordinal AS ordinal FROM sentences s WHERE s.document_id = %(doc)s "
            "ORDER BY s.ordinal DESC LIMIT 1",
            {"doc": document_id},
        )
        if last:
            total = int(last[0]["ordinal"]) + 1
    if total <= 0:
        return _empty_outline(
            "This document has no sentences yet — it may still be processing. "
            "Poll it with get_document before reading."
        )

    lines = await _rows(conn, _HEADING_LINES_SQL, {"doc": document_id, "cap": _HEADING_SCAN_CAP})
    headings: list[tuple[int, str, int]] = []
    for line in lines:
        parsed = outline_domain.parse_heading_lines(line["line"])
        if parsed:
            level, title, _ = parsed[0]
            headings.append((level, title, int(line["char_start"])))

    if len(headings) >= 2:
        kept, dropped = outline_domain.select_headings(headings, max_entries)
        resolved = await _rows(
            conn,
            _RESOLVE_OFFSETS_SQL,
            {"doc": document_id, "starts": [h[2] for h in kept]},
        )
        by_index = {int(r["idx"]): r for r in resolved if r["ordinal"] is not None}
        located = [
            (
                level,
                title,
                char_start,
                int(by_index[i + 1]["ordinal"]),
                by_index[i + 1]["page_number"],
            )
            for i, (level, title, char_start) in enumerate(kept)
            if i + 1 in by_index
        ]
        entries = outline_domain.build_heading_outline(located, total, total_chars)
        if entries:
            note = (
                f"Structure from {len(entries)} detected headings. Heading detection is "
                "heuristic — markup for EPUB/DOCX/MD, relative font size for PDFs — so a "
                "document can be mapped coarsely or unevenly."
            )
            if dropped:
                note += f" {dropped} deeper heading(s) omitted to fit max_entries."
            return {
                "source": "headings",
                "total_sentences": total,
                "entry_count": len(entries),
                "note": note,
                "entries": [vars(entry) for entry in entries],
            }

    buckets = outline_domain.plan_uniform_buckets(total, max_entries)
    rows = await _rows(
        conn,
        _BUCKET_SENTENCES_SQL,
        {"doc": document_id, "ordinals": [start for start, _ in buckets], "chars": _LABEL_CHARS},
    )
    opening = {int(r["ordinal"]): r for r in rows}
    entries_rows: list[Row] = []
    for start, end in buckets:
        row = opening.get(start, {})
        label = (row.get("label") or "").strip().replace("\n", " ")
        entries_rows.append(
            {
                "level": 1,
                "title": label or f"Sentences {start}–{end}",
                "ordinal_start": start,
                "ordinal_end": end,
                "char_start": int(row["char_start"]) if row.get("char_start") is not None else 0,
                "char_end": int(row["char_end"]) if row.get("char_end") is not None else 0,
                "page_start": row.get("page_number"),
                "page_end": None,
            }
        )
    return {
        "source": "uniform",
        "total_sentences": total,
        "entry_count": len(entries_rows),
        "note": (
            "No headings were detected in this document, so this is an even "
            f"{len(entries_rows)}-way split of its {total} sentences, each labelled with the "
            "sentence it opens on. It is a navigation grid for aiming "
            "read_document_sentences — not a table of contents."
        ),
        "entries": entries_rows,
    }


async def entity_profiles(
    conn: _AConn,
    *,
    document_id: str | None = None,
    entity_type: str | None = None,
    limit: int = 50,
) -> list[Row]:
    """Return reconciled entity profiles, most mentioned first.

    Reads the existing frequency path, folds spellings and types together with
    :func:`doc_ray.domain.entity_profiles.reconcile`, then enriches each profile
    with its canonical registry row where one exists. A profile with no registry
    match is returned with ``resolved=False`` rather than dressed up as a
    canonical identity.

    Args:
        conn: Async database connection (RLS decides visibility).
        document_id: Scope to one document, or ``None`` for the whole corpus.
        entity_type: Optional raw-type filter applied *before* grouping — useful
            for narrowing, but note it will hide the other types of a split
            entity, which is the very thing profiles exist to reveal.
        limit: Maximum profiles to return.

    Returns:
        Rows carrying the profile fields plus ``canonical_id``, ``canonical_name``,
        ``wikidata_id`` and ``resolved``.
    """
    raw = await read.entity_frequency(
        conn,
        document_id=document_id,
        entity_type=entity_type,
        limit=max(limit * _RAW_ROWS_PER_PROFILE, limit),
    )
    profiles = reconcile(raw, limit=limit)
    if not profiles:
        return []

    canonical: dict[str, Row] = {}
    for row in await _rows(
        conn, _CANONICAL_SQL, {"keys": [profile.normalized_form for profile in profiles]}
    ):
        canonical.setdefault(row["form_norm"], row)

    rows: list[Row] = []
    for profile in profiles:
        match = canonical.get(profile.normalized_form)
        rows.append(
            {
                "surface": profile.surface,
                "normalized_form": profile.normalized_form,
                "mentions": profile.mentions,
                "dominant_type": profile.dominant_type,
                "type_spread": [
                    {"entity_type": t.entity_type, "count": t.count} for t in profile.type_spread
                ],
                "type_consistency": profile.type_consistency,
                "surface_forms": list(profile.surface_forms),
                "canonical_id": str(match["canonical_id"]) if match else None,
                "canonical_name": match["preferred"] if match else None,
                "wikidata_id": (match.get("wikidata_id") if match else None),
                "resolved": match is not None,
            }
        )
    return rows
