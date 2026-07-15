"""Corpus read tools backed by the doc-ray GraphQL API, RLS-scoped by the forwarded token.

Each tool forwards the MCP caller's bearer token (stashed in :data:`caller_token` by the
auth middleware) to ``DOCRAY_API_URL /graphql`` — the exact pattern of
``webapp/graphql_client.py`` — so the API's ``HeaderTokenAuthenticator`` resolves the same
principal and Postgres RLS scopes exactly what that principal may read. The MCP layer holds
no database pool and grants nothing the GraphQL API wouldn't.
"""

from __future__ import annotations

import contextvars
import os
from collections.abc import Callable
from typing import Any

import httpx

#: GraphQL endpoint to forward to (mirrors webapp/graphql_client.py).
API_URL = os.environ.get("DOCRAY_API_URL", "http://localhost:8000/graphql")
_TIMEOUT = float(os.environ.get("DOCRAY_MCP_TIMEOUT_SECONDS", "30"))

#: The caller's bearer token for the current request, set by BearerAuthMiddleware and
#: forwarded on every GraphQL call so the API applies that principal's RLS. Empty =
#: anonymous (no Authorization header sent).
caller_token: contextvars.ContextVar[str] = contextvars.ContextVar(
    "docray_mcp_caller_token", default=""
)


async def gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute a GraphQL operation, forwarding the caller's bearer token.

    Args:
        query: The GraphQL query string.
        variables: The GraphQL variables mapping.

    Returns:
        The ``data`` payload of the response.

    Raises:
        RuntimeError: If the response carries GraphQL ``errors`` (HTTP 200-wrapped).
    """
    headers: dict[str, str] = {"content-type": "application/json"}
    token = caller_token.get()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            API_URL, json={"query": query, "variables": variables}, headers=headers
        )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
    data: dict[str, Any] = body["data"]
    return data


async def search_sentences(
    term: str, document_id: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Keyword-in-context search across the corpus you can read (pre/hit/post windows)."""
    data = await gql(
        "query($t:String!,$d:ID,$l:Int!){searchSentences(term:$t,documentId:$d,limit:$l)"
        "{id documentId title ordinal pre hit post cursor}}",
        {"t": term, "d": document_id, "l": limit},
    )
    hits: list[dict[str, Any]] = data["searchSentences"]
    return hits


async def full_text_search(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Rank documents by full-text relevance to a query."""
    data = await gql(
        "query($q:String!,$l:Int!){fullTextSearch(query:$q,limit:$l){documentId title rank}}",
        {"q": query, "l": limit},
    )
    hits: list[dict[str, Any]] = data["fullTextSearch"]
    return hits


async def list_documents(
    limit: int = 25, doc_type: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    """List documents visible to you, newest first."""
    data = await gql(
        "query($l:Int!,$dt:String,$s:String){documents(limit:$l,docType:$dt,status:$s)"
        "{id title docType status pageCount charCount createdAt cursor}}",
        {"l": limit, "dt": doc_type, "s": status},
    )
    docs: list[dict[str, Any]] = data["documents"]
    return docs


async def get_document(document_id: str) -> dict[str, Any] | None:
    """Fetch one document's metadata and counts by id."""
    data = await gql(
        "query($id:ID!){document(id:$id){id title docType status language pageCount charCount "
        "sentenceCount tokenCount authors tags createdAt}}",
        {"id": document_id},
    )
    doc: dict[str, Any] | None = data["document"]
    return doc


async def entity_frequency(
    document_id: str | None = None, entity_type: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Most frequent named entities across the corpus or one document."""
    data = await gql(
        "query($d:ID,$e:String,$l:Int!){entityFrequency(documentId:$d,entityType:$e,limit:$l)"
        "{text entityType count}}",
        {"d": document_id, "e": entity_type, "l": limit},
    )
    rows: list[dict[str, Any]] = data["entityFrequency"]
    return rows


async def lexeme_kwic(
    uid: str, document_id: str | None = None, limit: int = 40
) -> list[dict[str, Any]]:
    """Keyword-in-context concordance for a lexeme (KWIC = Lexeme.occurrences, not a root field)."""
    data = await gql(
        "query($u:ID!,$d:ID,$l:Int!){lexeme(uid:$u){uid lemma pos "
        "occurrences(documentId:$d,limit:$l)"
        "{documentId title charStart surface kwic pre hit post}}}",
        {"u": uid, "d": document_id, "l": limit},
    )
    lexeme = data.get("lexeme") or {}
    occ: list[dict[str, Any]] = lexeme.get("occurrences", [])
    return occ


#: The read tools registered on the MCP server (see server.py).
TOOLS: tuple[Callable[..., Any], ...] = (
    search_sentences,
    full_text_search,
    list_documents,
    get_document,
    entity_frequency,
    lexeme_kwic,
)
