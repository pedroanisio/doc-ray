"""MCP document-level full-text search tool."""

from __future__ import annotations

from typing import Any


async def full_text_search(
    query: str, limit: int = 25, mode: str = "terms"
) -> list[dict[str, Any]]:
    """Rank whole Documents by language-aware full-text relevance.

    ``mode='terms'`` (the default) requires all stemmed terms in any positions.
    ``mode='phrase'`` requires the normalized terms in order. Both modes use the
    Document's language stemming and stop-word rules; neither accepts raw tsquery
    syntax. Use the returned Document id with ``search_sentences`` to locate a
    cited passage or ``read_document_sentences`` to read an ordinal range.
    """
    # Import lazily so corpus can re-export this tool without a module cycle; the
    # shared GraphQL/error helpers remain the single transport contract.
    from doc_ray.entrypoints.mcp import corpus

    corpus._require_nonempty(query, "query")
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"terms", "phrase"}:
        raise corpus.ToolCallError(
            "bad_input",
            "mode must be 'terms' or 'phrase'",
            hint="use 'terms' for all stemmed terms in any positions or 'phrase' for order",
        )
    data = await corpus.gql(
        "query($q:String!,$l:Int!,$m:FullTextSearchMode!){"
        "fullTextSearch(query:$q,limit:$l,mode:$m){documentId title rank}}",
        {"q": query, "l": corpus.effective_limit(limit), "m": normalized_mode.upper()},
    )
    return list(data["fullTextSearch"])
