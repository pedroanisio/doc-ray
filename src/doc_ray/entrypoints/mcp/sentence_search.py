"""Phrase-mode GraphQL operation for the MCP sentence-search tool (#45)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

GraphQLCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


async def phrase_page(
    call: GraphQLCall,
    fields: str,
    *,
    term: str,
    document_id: str | None,
    limit: int,
    after: str | None,
) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    """Fetch the metadata-bearing phrase page and normalize scan evidence."""
    data = await call(
        "query($t:String!,$d:ID,$l:Int!,$a:String,$m:SentenceMatchMode!)"
        f"{{searchSentencePage(term:$t,documentId:$d,limit:$l,after:$a,match:$m)"
        f"{{hits{{{fields} occurrences{{charStart charEnd}} occurrenceCount}} "
        "matchProfile candidateScanned candidateLimit candidateLimitReached}}",
        {"t": term, "d": document_id, "l": limit, "a": after, "m": "PHRASE"},
    )
    page = data["searchSentencePage"]
    candidate = {
        "candidate_scanned": page["candidateScanned"],
        "candidate_limit": page["candidateLimit"],
        "candidate_limit_reached": page["candidateLimitReached"],
    }
    return page["hits"], str(page["matchProfile"]), candidate
