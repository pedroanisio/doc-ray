"""MCP read wrapper for the untrusted Tier-3 visual-caption review queue."""

from __future__ import annotations

from typing import Any


async def visual_review(
    document_id: str | None = None,
    status: str = "pending",
    limit: int = 100,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """List RLS-scoped, untrusted Tier-3 caption proposals awaiting a human trust decision."""
    from doc_ray.entrypoints.mcp.corpus import ToolCallError, _require_uuid, effective_limit, gql

    _require_uuid(document_id, "document_id")
    if status not in {"pending", "approved", "rejected"}:
        raise ToolCallError("bad_input", "status must be pending, approved, or rejected")
    data = await gql(
        "query($d:ID,$s:String!,$l:Int!,$a:String)"
        "{visualReview(documentId:$d,status:$s,limit:$l,after:$a)"
        "{id documentImageId documentId proposed groundingScore groundingEvidence "
        "status reviewer createdAt updatedAt pageNumber objectKey cursor}}",
        {"d": document_id, "s": status, "l": effective_limit(limit), "a": after},
    )
    rows: list[dict[str, Any]] = data["visualReview"]
    return rows
