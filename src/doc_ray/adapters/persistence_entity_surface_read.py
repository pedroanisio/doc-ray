"""Bounded occurrence preview for exact-surface NER correction rules.

The preview is deliberately not a corpus aggregate. Callers provide at most 200
document ids, and one lateral lookup reads at most ``count_cap + 1`` exact
``(surface, entity_type)`` matches per document. The extra row makes truncation
explicit without pretending the capped result is a complete count.
"""

from __future__ import annotations

from typing import Any

import psycopg

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]

MAX_DOCUMENTS = 200
MAX_SAMPLE_LIMIT = 20
MAX_COUNT_CAP = 10_000

_OCCURRENCES_SQL = """
    SELECT target.document_id, hit.mention_id, hit.char_start, hit.char_end,
           sentence.ordinal AS sentence_ordinal,
           sentence.text AS sentence_text
    FROM unnest(%(document_ids)s::uuid[]) WITH ORDINALITY
         AS target(document_id, document_order)
    LEFT JOIN LATERAL (
        SELECT em.id AS mention_id, sp.char_start, sp.char_end
        FROM entity_mention em
        JOIN annotations an ON an.id = em.annotation_id
        JOIN spans sp ON sp.id = em.span_id
        WHERE an.document_id = target.document_id
          AND em.surface = %(surface)s
          AND em.entity_type = %(original_type)s
        ORDER BY sp.char_start, sp.char_end, em.id
        LIMIT %(scan_limit)s
    ) hit ON TRUE
    LEFT JOIN LATERAL (
        SELECT s.ordinal, s.text
        FROM sentences s
        JOIN spans sentence_span ON sentence_span.id = s.span_id
        WHERE s.document_id = target.document_id
          AND sentence_span.char_start <= hit.char_start
          AND sentence_span.char_end >= hit.char_end
        ORDER BY sentence_span.char_end - sentence_span.char_start, s.ordinal
        LIMIT 1
    ) sentence ON hit.mention_id IS NOT NULL
    ORDER BY target.document_order, hit.char_start, hit.char_end, hit.mention_id
"""


def _validate(
    document_ids: list[str],
    surface: str,
    original_type: str,
    sample_limit: int,
    count_cap: int,
) -> None:
    """Reject inputs that would make the preview ambiguous or unbounded."""
    if not document_ids:
        raise ValueError("document_ids must contain at least one document")
    if len(document_ids) > MAX_DOCUMENTS:
        raise ValueError(f"document_ids accepts at most {MAX_DOCUMENTS} documents")
    if not surface or not surface.strip():
        raise ValueError("surface must not be blank")
    if not original_type or not original_type.strip():
        raise ValueError("original_type must not be blank")
    if not 1 <= sample_limit <= MAX_SAMPLE_LIMIT:
        raise ValueError(f"sample_limit must be between 1 and {MAX_SAMPLE_LIMIT}")
    if not 1 <= count_cap <= MAX_COUNT_CAP:
        raise ValueError(f"count_cap must be between 1 and {MAX_COUNT_CAP}")


def _empty_result(document_id: str, surface: str, original_type: str, count_cap: int) -> Row:
    """Build one explicit zero-match result before folding query rows into it."""
    return {
        "document_id": document_id,
        "surface": surface,
        "original_type": original_type,
        "occurrence_count": 0,
        "count_cap": count_cap,
        "count_capped": False,
        "samples": [],
    }


async def entity_surface_occurrences(
    conn: _AConn,
    document_ids: list[str],
    *,
    surface: str,
    original_type: str,
    sample_limit: int = 5,
    count_cap: int = 1000,
) -> list[Row]:
    """Preview exact surface + current-type matches in explicitly named documents.

    Counts are capped per document and ``count_capped`` tells the caller when at
    least one additional match exists. Samples carry the sentence containing the
    mention when available. Matching uses PostgreSQL ``=`` on both fields: no
    case folding, fuzzy expansion, regex, or implicit corpus-wide scope.
    """
    _validate(document_ids, surface, original_type, sample_limit, count_cap)
    ordered_ids = list(dict.fromkeys(document_ids))
    results = {
        document_id: _empty_result(document_id, surface, original_type, count_cap)
        for document_id in ordered_ids
    }
    cur = await conn.execute(
        _OCCURRENCES_SQL,
        {
            "document_ids": ordered_ids,
            "surface": surface,
            "original_type": original_type,
            "scan_limit": count_cap + 1,
        },
    )
    rows = await cur.fetchall()
    seen: dict[str, int] = {document_id: 0 for document_id in ordered_ids}
    for row in rows:
        document_id = str(row["document_id"])
        mention_id = row.get("mention_id")
        if mention_id is None or document_id not in results:
            continue
        seen[document_id] += 1
        result = results[document_id]
        result["occurrence_count"] = min(seen[document_id], count_cap)
        result["count_capped"] = seen[document_id] > count_cap
        samples = result["samples"]
        assert isinstance(samples, list)
        if len(samples) < sample_limit:
            samples.append(
                {
                    "mention_id": str(mention_id),
                    "char_start": row["char_start"],
                    "char_end": row["char_end"],
                    "sentence_ordinal": row.get("sentence_ordinal"),
                    "sentence_context": row.get("sentence_text"),
                }
            )
    return [results[document_id] for document_id in ordered_ids]
