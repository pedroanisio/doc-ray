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

#: Per-window character cap for KWIC text fields. Upstream sentence segmentation
#: can produce mega-sentences (e.g. a whole table of contents extracted as one
#: sentence); windows are clipped with an explicit marker so tool responses stay
#: bounded and truncation is never silent.
MAX_KWIC_WINDOW_CHARS = 500


def clip_text(text: str | None, cap: int) -> str | None:
    """Clip *text* to *cap* characters, marking exactly how much was dropped."""
    if text is None or len(text) <= cap:
        return text
    return f"{text[:cap]}… [+{len(text) - cap} chars]"


def _clip_windows(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Clip each KWIC text window of *row* to :data:`MAX_KWIC_WINDOW_CHARS`."""
    for field in fields:
        value = row.get(field)
        if isinstance(value, str):
            row[field] = clip_text(value, MAX_KWIC_WINDOW_CHARS)
    return row


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
    term: str, document_id: str | None = None, limit: int = 20, after: str | None = None
) -> list[dict[str, Any]]:
    """Keyword-in-context search across the corpus you can read (pre/hit/post windows).

    Pass a row's ``cursor`` as ``after`` to fetch the next page. Oversized windows
    are clipped with an explicit ``… [+N chars]`` marker.
    """
    data = await gql(
        "query($t:String!,$d:ID,$l:Int!,$a:String)"
        "{searchSentences(term:$t,documentId:$d,limit:$l,after:$a)"
        "{id documentId title ordinal pre hit post cursor}}",
        {"t": term, "d": document_id, "l": limit, "a": after},
    )
    hits: list[dict[str, Any]] = data["searchSentences"]
    return [_clip_windows(hit, ("pre", "hit", "post")) for hit in hits]


async def full_text_search(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Rank documents by full-text relevance to a query."""
    data = await gql(
        "query($q:String!,$l:Int!){fullTextSearch(query:$q,limit:$l){documentId title rank}}",
        {"q": query, "l": limit},
    )
    hits: list[dict[str, Any]] = data["fullTextSearch"]
    return hits


async def list_documents(
    limit: int = 25,
    doc_type: str | None = None,
    status: str | None = None,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """List documents visible to you, newest first.

    Pass a row's ``cursor`` as ``after`` to fetch the next page.
    """
    data = await gql(
        "query($l:Int!,$dt:String,$s:String,$a:String)"
        "{documents(limit:$l,docType:$dt,status:$s,after:$a)"
        "{id title docType status pageCount charCount createdAt cursor}}",
        {"l": limit, "dt": doc_type, "s": status, "a": after},
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
    """Keyword-in-context concordance for a lexeme (KWIC = Lexeme.occurrences, not a root field).

    Oversized windows are clipped with an explicit ``… [+N chars]`` marker.
    """
    data = await gql(
        "query($u:ID!,$d:ID,$l:Int!){lexeme(uid:$u){uid lemma pos "
        "occurrences(documentId:$d,limit:$l)"
        "{documentId title charStart surface kwic pre hit post}}}",
        {"u": uid, "d": document_id, "l": limit},
    )
    lexeme = data.get("lexeme") or {}
    occ: list[dict[str, Any]] = lexeme.get("occurrences", [])
    return [_clip_windows(row, ("kwic", "pre", "hit", "post")) for row in occ]


async def document_images(
    document_id: str, limit: int = 100, after: str | None = None
) -> list[dict[str, Any]]:
    """Detected images/regions in a document with deterministic Tier-1 visual features.

    Each row: page, source (embedded-raster / full-page), size, image class,
    saturation/value/contrast/edge-density, and the dominant colour palette.
    """
    data = await gql(
        "query($d:ID!,$l:Int!,$a:String){documentImages(documentId:$d,limit:$l,after:$a)"
        "{id pageNumber source widthPx heightPx imageClass meanSaturation meanValue "
        "contrast edgeDensity aspectRatio dominantColors{hex proportion} cursor}}",
        {"d": document_id, "l": limit, "a": after},
    )
    rows: list[dict[str, Any]] = data["documentImages"]
    return rows


async def image_class_summary(document_id: str) -> list[dict[str, Any]]:
    """The ``(imageClass, count)`` distribution of a document's detected images."""
    data = await gql(
        "query($d:ID!){imageClassSummary(documentId:$d){imageClass count}}",
        {"d": document_id},
    )
    rows: list[dict[str, Any]] = data["imageClassSummary"]
    return rows


async def cross_modal_image_search(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Find images by meaning from a text query (text->image cross-modal retrieval).

    Ranks the corpus's images by CLIP/SigLIP cosine similarity to *query*; returns
    document/page/class and a similarity score. RLS-scoped to readable images.
    """
    data = await gql(
        "query($q:String!,$l:Int!){crossModalImageSearch(query:$q,limit:$l)"
        "{id documentId pageNumber source imageClass similarity}}",
        {"q": query, "l": limit},
    )
    rows: list[dict[str, Any]] = data["crossModalImageSearch"]
    return rows


async def visual_captions(document_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """A document's COMMITTED, grounded first-tier VLM captions (trusted only).

    Only captions whose grounding cleared the gate are returned; low-confidence
    captions stay in human review and are never surfaced here.
    """
    data = await gql(
        "query($d:ID!,$l:Int!){visualCaptions(documentId:$d,limit:$l)"
        "{id documentImageId pageNumber caption model groundingScore}}",
        {"d": document_id, "l": limit},
    )
    rows: list[dict[str, Any]] = data["visualCaptions"]
    return rows


#: The read tools registered on the MCP server (see server.py).
TOOLS: tuple[Callable[..., Any], ...] = (
    search_sentences,
    full_text_search,
    list_documents,
    get_document,
    entity_frequency,
    lexeme_kwic,
    document_images,
    image_class_summary,
    cross_modal_image_search,
    visual_captions,
)
