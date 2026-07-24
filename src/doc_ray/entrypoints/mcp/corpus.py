"""Corpus read tools backed by the doc-ray GraphQL API, RLS-scoped by the forwarded token.

Each tool forwards the MCP caller's bearer token (resolved per call by
:func:`~doc_ray.entrypoints.mcp.tokens.current_token`) to ``DOCRAY_API_URL /graphql`` — the
exact pattern of ``webapp/graphql_client.py`` — so the API's ``HeaderTokenAuthenticator``
resolves the same principal and Postgres RLS scopes exactly what that principal may read.
The MCP layer holds no database pool and grants nothing the GraphQL API wouldn't.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from doc_ray.entrypoints.mcp.tokens import caller_token, current_token

#: GraphQL endpoint to forward to (mirrors webapp/graphql_client.py).
API_URL = os.environ.get("DOCRAY_API_URL", "http://localhost:8000/graphql")
_TIMEOUT = float(os.environ.get("DOCRAY_MCP_TIMEOUT_SECONDS", "30"))

#: Re-exported for callers and tests; the request-aware resolver is
#: :func:`~doc_ray.entrypoints.mcp.tokens.current_token`.
__all__ = ["caller_token", "current_token"]

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


class ToolCallError(RuntimeError):
    """Typed MCP tool failure: a stable ``kind``, retriability, and agent hint.

    Kinds: ``bad_input`` (fix the arguments), ``permission_denied`` (fix the
    token/scopes), ``not_found``, ``unsupported`` (this deployment cannot serve
    the capability — never retriable), ``upstream_unavailable`` (retriable), and
    ``upstream_error`` (unclassified upstream failure). Subclasses
    ``RuntimeError`` so existing callers that caught the old raw errors keep
    working.
    """

    def __init__(
        self, kind: str, message: str, *, retriable: bool = False, hint: str | None = None
    ) -> None:
        """Render ``[kind] message (retriable) — hint`` as the exception text."""
        self.kind = kind
        self.retriable = retriable
        self.hint = hint
        rendered = f"[{kind}] {message}"
        if retriable:
            rendered += " (retriable)"
        if hint:
            rendered += f" — {hint}"
        super().__init__(rendered)


#: Agent-facing hints attached to classified GraphQL errors, by kind.
_KIND_HINTS = {
    "unsupported": (
        "this deployment does not serve the capability — do not retry; "
        "use text-side tools (full_text_search, search_sentences, visual_captions) instead"
    ),
}


def _classify_graphql_error(message: str) -> str:
    """Map a GraphQL error message onto a stable ToolCallError kind."""
    lowered = message.lower()
    # The API's write guards raise these exact authz phrasings (mutation_guards.py:
    # "requires an authenticated principal with write scope", "no write grant for
    # document …", "cannot share document …") — all are permission failures the
    # agent fixes by acquiring scope/grant, not by retrying.
    if any(
        marker in lowered
        for marker in (
            "permission denied",
            "not authorized",
            "forbidden",
            "write scope",
            "write grant",
            "cannot share",
        )
    ):
        return "permission_denied"
    # "not installed" is the API's typed vision-stack error (visual_query.py);
    # "no module named" is the raw leak from API hosts predating that error.
    if "not installed" in lowered or "no module named" in lowered:
        return "unsupported"
    if "not found" in lowered or "does not exist" in lowered:
        return "not_found"
    if any(marker in lowered for marker in ("invalid", "expected", "must be", "cannot represent")):
        return "bad_input"
    return "upstream_error"


def _require_uuid(value: str | None, param: str) -> str | None:
    """Reject a malformed document id before it costs a GraphQL round-trip."""
    if value is None:
        return None
    try:
        uuid.UUID(str(value))
    except ValueError:
        raise ToolCallError(
            "bad_input",
            f"{param} must be a document UUID, got {value!r}",
            hint="take ids from list_documents or full_text_search",
        ) from None
    return value


def _require_nonempty(value: str, param: str) -> str:
    """Reject blank required strings before any network call."""
    if not value or not value.strip():
        raise ToolCallError("bad_input", f"{param} must be a non-empty string")
    return value


async def gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Execute a GraphQL operation, forwarding the caller's bearer token.

    Args:
        query: The GraphQL query string.
        variables: The GraphQL variables mapping.

    Returns:
        The ``data`` payload of the response.

    Raises:
        ToolCallError: Typed failure — transport, HTTP status, and GraphQL
            ``errors`` are all mapped onto a stable ``kind`` with a
            retriability hint.
    """
    headers: dict[str, str] = {"content-type": "application/json"}
    token = current_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                API_URL, json={"query": query, "variables": variables}, headers=headers
            )
    except httpx.TimeoutException as exc:
        raise ToolCallError(
            "upstream_unavailable",
            f"GraphQL API timed out after {_TIMEOUT:g}s",
            retriable=True,
            hint="retry, or narrow the query",
        ) from exc
    except httpx.TransportError as exc:
        raise ToolCallError(
            "upstream_unavailable", "GraphQL API unreachable", retriable=True
        ) from exc
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (401, 403):
            raise ToolCallError(
                "permission_denied",
                f"GraphQL API returned HTTP {status}",
                hint="check the bearer token and its scopes",
            ) from exc
        raise ToolCallError(
            "upstream_unavailable" if status >= 500 else "upstream_error",
            f"GraphQL API returned HTTP {status}",
            retriable=status >= 500,
        ) from exc
    body = response.json()
    if body.get("errors"):
        message = body["errors"][0].get("message", "GraphQL error")
        kind = _classify_graphql_error(message)
        raise ToolCallError(kind, message, hint=_KIND_HINTS.get(kind))
    data: dict[str, Any] = body["data"]
    return data


async def search_sentences(
    term: str, document_id: str | None = None, limit: int = 20, after: str | None = None
) -> list[dict[str, Any]]:
    """Keyword-in-context search across the corpus you can read (pre/hit/post windows).

    Pass a row's ``cursor`` as ``after`` to fetch the next page. Oversized windows
    are clipped with an explicit ``… [+N chars]`` marker.
    """
    _require_nonempty(term, "term")
    _require_uuid(document_id, "document_id")
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
    _require_nonempty(query, "query")
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


#: The ``Job`` selection set shared by ``get_job`` and every write tool
#: (``mutations.py``), so an enqueued job and a polled job return the same shape.
JOB_FIELDS = (
    "id kind status documentId createdAt startedAt finishedAt attempts maxAttempts "
    "error progressStage progressCurrent progressTotal"
)


async def get_job(job_id: str) -> dict[str, Any] | None:
    """Poll one background job's status by id (returns None if unknown/not visible).

    Mutations (reannotate, annotate, canonicalize, …) enqueue work and return a
    job whose ``status`` is ``queued``; call this to watch it progress to
    ``running`` then ``succeeded``/``failed``.
    """
    _require_nonempty(job_id, "job_id")
    data = await gql(
        f"query($id:ID!){{job(id:$id){{{JOB_FIELDS}}}}}",
        {"id": job_id},
    )
    job: dict[str, Any] | None = data["job"]
    return job


#: The ``DocumentCollection`` metadata selection shared by the read and write
#: collection tools (``mutations.py``), so a listed, fetched, or mutated
#: collection returns the same shape. ``documents`` is added only by ``get_collection``.
COLLECTION_FIELDS = "id name description kind ruleSummary documentCount createdAt updatedAt cursor"


async def list_collections(
    limit: int = 50, kind: str | None = None, after: str | None = None
) -> list[dict[str, Any]]:
    """List document collections you can see (manual groupings and smart rules).

    Collections are doc-ray's native container for grouping documents — prefer
    them over ad-hoc tags. Pass ``kind`` to filter (e.g. ``manual``); pass a
    row's ``cursor`` as ``after`` to page.
    """
    data = await gql(
        "query($l:Int!,$k:String,$a:String){collections(limit:$l,kind:$k,after:$a)"
        f"{{{COLLECTION_FIELDS}}}}}",
        {"l": limit, "k": kind, "a": after},
    )
    rows: list[dict[str, Any]] = data["collections"]
    return rows


async def get_collection(collection_id: str, document_limit: int = 50) -> dict[str, Any] | None:
    """Fetch one collection with its member documents (None if unknown/not visible)."""
    _require_uuid(collection_id, "collection_id")
    data = await gql(
        "query($id:ID!,$dl:Int!){collection(id:$id)"
        f"{{{COLLECTION_FIELDS} documents(limit:$dl){{id title docType status}}}}}}",
        {"id": collection_id, "dl": document_limit},
    )
    collection: dict[str, Any] | None = data["collection"]
    return collection


async def get_document(document_id: str) -> dict[str, Any] | None:
    """Fetch one document's metadata and counts by id."""
    _require_uuid(document_id, "document_id")
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
    _require_uuid(document_id, "document_id")
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
    _require_nonempty(uid, "uid")
    _require_uuid(document_id, "document_id")
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
    _require_uuid(document_id, "document_id")
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
    _require_uuid(document_id, "document_id")
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
    Requires the vision stack on the API host; without it the call fails with
    kind ``unsupported`` — do not retry, use the text-side tools instead.
    """
    _require_nonempty(query, "query")
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
    _require_uuid(document_id, "document_id")
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
    get_job,
    list_collections,
    get_collection,
    entity_frequency,
    lexeme_kwic,
    document_images,
    image_class_summary,
    cross_modal_image_search,
    visual_captions,
)
