"""MCP semantic-retrieval tool (NLP Layer 20).

A separate module (like ``structure`` / ``compose``) so the corpus tool file
stays under the D-015 size cap. Delegates through :func:`corpus.gql`, inheriting
the same token forwarding and RLS scoping as every other read tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doc_ray.entrypoints.mcp.corpus import _require_nonempty, _require_uuid, gql


async def semantic_search_sentences(
    query: str, document_id: str | None = None, limit: int = 25
) -> list[dict[str, Any]]:
    """Find sentences by MEANING (learned embeddings), not keywords (RLS-scoped).

    Ranks sentences by learned semantic similarity to *query* — a paraphrase can
    match even with no shared words. This is the antidote to lexical
    ``search_sentences`` flooding on a common term: use it when you want the most
    relevant passages regardless of exact wording. Returns sentence text,
    document, ordinal, and a similarity score; pass ``document_id`` to scope to
    one document. Requires the semantic stack on the API host; without it the
    call fails with kind ``unsupported`` — do not retry, fall back to
    ``search_sentences``.
    """
    _require_nonempty(query, "query")
    if document_id is not None:
        _require_uuid(document_id, "document_id")
    data = await gql(
        "query($q:String!,$l:Int!,$d:ID){"
        "semanticSearchSentences(query:$q,limit:$l,documentId:$d)"
        "{sentenceId documentId ordinal text similarity}}",
        {"q": query, "l": limit, "d": document_id},
    )
    rows: list[dict[str, Any]] = data["semanticSearchSentences"]
    return rows


#: The semantic read tool registered on the MCP server (see server.py).
TOOLS: tuple[Callable[..., Any], ...] = (semantic_search_sentences,)
