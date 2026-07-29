"""MCP semantic-retrieval tool (NLP Layer 20).

A separate module (like ``structure`` / ``compose``) so the corpus tool file
stays under the D-015 size cap. Delegates through :func:`corpus.gql`, inheriting
the same token forwarding and RLS scoping as every other read tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doc_ray.entrypoints.mcp.corpus import (
    ToolCallError,
    _require_nonempty,
    _require_uuid,
    gql,
)

#: What to do about an unembedded scope. Named as a tool call because the remedy
#: is one: embedding is a worker job the caller can enqueue, not a deployment
#: capability they have to wait for.
_EMBED_HINT = (
    "run embed_document_sentences(document_id=…) to embed it (worker job — poll "
    "with get_job), or use search_sentences / full_text_search for lexical "
    "retrieval meanwhile"
)


def _no_vectors_message(document_id: str | None) -> str:
    """State what an empty ranking actually proves, and what it does not.

    The distinction this preserves: the ranking has no similarity threshold, so
    zero rows is never "nothing was close enough" — it is "there was nothing to
    rank". Returning ``[]`` let a caller record the first, which is a fabricated
    negative finding about the corpus's content.
    """
    scope = f"document {document_id}" if document_id else "the corpus visible to you"
    return (
        f"No embedded sentences in scope: {scope} has no sentence vectors for the "
        f"active embedding model. This ranking applies no similarity floor, so this "
        f"is NOT a finding that nothing was semantically similar — nothing was "
        f"searched. Ingested documents are embedded automatically, so this "
        f"normally means the document's chained embed job has not finished or "
        f"failed; a model cutover also leaves prior-model vectors invisible here."
    )


async def semantic_search_sentences(
    query: str, document_id: str | None = None, limit: int = 25
) -> list[dict[str, Any]]:
    """Find sentences by MEANING (learned embeddings), not keywords (RLS-scoped).

    Ranks sentences by learned semantic similarity to *query* — a paraphrase can
    match even with no shared words. This is the antidote to lexical
    ``search_sentences`` flooding on a common term: use it when you want the most
    relevant passages regardless of exact wording. Returns sentence text,
    document, ordinal, and a similarity score; pass ``document_id`` to scope to
    one document.

    **Every ingested document is embedded automatically** — embedding is part of
    the default post-ingest chain, so the whole corpus is reachable here. A
    document can still be temporarily absent while its chained embed job is
    queued or if that job failed. Because the ranking applies no similarity
    floor, an empty result can only mean "nothing in this scope is embedded",
    never "nothing was similar"; that case therefore fails with kind
    ``not_found`` naming the document, instead of returning ``[]`` for you to
    misread as an answer.

    Requires the semantic stack on the API host; without it the call fails with
    kind ``unsupported`` — do not retry, fall back to ``search_sentences``.
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
    if not rows:
        raise ToolCallError("not_found", _no_vectors_message(document_id), hint=_EMBED_HINT)
    return rows


#: The semantic read tool registered on the MCP server (see server.py).
TOOLS: tuple[Callable[..., Any], ...] = (semantic_search_sentences,)
