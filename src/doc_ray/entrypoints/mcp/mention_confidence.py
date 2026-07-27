"""MCP access to the materialized mention-confidence review queue."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doc_ray.entrypoints.mcp.corpus import _require_uuid, gql


async def uncertain_mentions(
    document_id: str,
    limit: int = 100,
    entity_type: str | None = None,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """Rank uncorrected entity mentions for review, least trustworthy first.

    ``confidence`` is a deterministic TRIAGE RANK, not a calibrated model
    probability. Inspect ``reasons`` before acting. The queue reads persisted
    scores, excludes spans already covered by a human correction, and is
    RLS-scoped to the caller. After reviewing a row, use
    ``correct_entity_mention`` to reclassify, reject, or adjust its stable
    ``charStart``/``charEnd`` span. Page with the last row's opaque ``cursor``.
    """
    _require_uuid(document_id, "document_id")
    data = await gql(
        "query($d:ID!,$n:Int!,$e:String,$a:String){uncertainMentions"
        "(documentId:$d,limit:$n,entityType:$e,after:$a){id documentId charStart "
        "charEnd surface entityType confidence reasons method cursor}}",
        {"d": document_id, "n": limit, "e": entity_type, "a": after},
    )
    rows: list[dict[str, Any]] = data["uncertainMentions"]
    return rows


TOOLS: tuple[Callable[..., Any], ...] = (uncertain_mentions,)
