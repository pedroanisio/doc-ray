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

from doc_ray.entrypoints.mcp.shaping import (
    MAX_KWIC_WINDOW_CHARS,
    MAX_PAGE_SIZE,
    _clip_windows,
    _search_coverage,
    clip_text,
    effective_limit,
)
from doc_ray.entrypoints.mcp.tokens import caller_token, current_token

#: GraphQL endpoint to forward to (mirrors webapp/graphql_client.py).
API_URL = os.environ.get("DOCRAY_API_URL", "http://localhost:8000/graphql")
_TIMEOUT = float(os.environ.get("DOCRAY_MCP_TIMEOUT_SECONDS", "30"))

#: Re-exported for callers and tests: the token resolvers (the request-aware
#: one is :func:`~doc_ray.entrypoints.mcp.tokens.current_token`) and the
#: response-shaping helpers that lived here before the D-015 split
#: (:mod:`doc_ray.entrypoints.mcp.shaping`).
__all__ = [
    "MAX_KWIC_WINDOW_CHARS",
    "MAX_PAGE_SIZE",
    "caller_token",
    "clip_text",
    "current_token",
    "effective_limit",
    "visual_review",
]


class ToolCallError(RuntimeError):
    """Typed MCP tool failure: a stable ``kind``, retriability, and agent hint.

    Kinds: ``bad_input`` (fix the arguments), ``permission_denied`` (fix the
    token/scopes), ``not_found``, ``unsupported`` (this deployment cannot serve
    the capability — never retriable), ``timeout`` (the query exceeded the API's
    statement budget; narrow the scope, do not repeat it unchanged),
    ``upstream_unavailable`` (retriable), and
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
    "timeout": (
        "the read exceeded the API statement budget — repeating it unchanged will "
        "time out again; narrow it (pass document_id, lower limit, use a rarer term) "
        "or address a range directly with read_document_sentences"
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
    # Postgres' own wording for the API pool's statement_timeout ("canceling
    # statement due to statement timeout"), plus the transport-side deadline.
    # Unclassified, this arrived as a bare upstream_error, which reads as "the
    # server is broken" rather than "this query was too wide" — the caller then
    # abandons a capability that works fine at a narrower scope.
    if "statement timeout" in lowered or "timed out" in lowered or "timeout" in lowered:
        return "timeout"
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


#: Verbosity levels for the bulk readers. ``detailed`` is the historical shape and
#: stays the default, so nothing that exists today changes behaviour.
RESPONSE_FORMATS = ("detailed", "concise")


def _require_response_format(value: str) -> str:
    """Reject an unknown response_format locally, naming the valid values.

    A silently-ignored format argument is worse than no argument at all: the
    agent believes it bought a saving it never got, and keeps budgeting for it.
    """
    if value not in RESPONSE_FORMATS:
        raise ToolCallError(
            "bad_input",
            f"response_format must be one of {', '.join(RESPONSE_FORMATS)}, got {value!r}",
            hint="use 'concise' to drop per-row metadata, 'detailed' for the full shape",
        )
    return value


def _project_concise(rows: list[dict[str, Any]], keep: tuple[str, ...]) -> list[dict[str, Any]]:
    """Keep only *keep* on each row, plus the final row's paging cursor.

    Only the last cursor of a page is ever used to continue, so carrying one on
    every row is pure overhead — but dropping it entirely would break paging, so
    the last row keeps its own.
    """
    projected = [{k: row[k] for k in keep if k in row} for row in rows]
    if projected and (cursor := rows[-1].get("cursor")) is not None:
        projected[-1]["cursor"] = cursor
    return projected


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
        hint = _KIND_HINTS.get(kind)
        # Cursors are opaque and per-tool: a search_sentences cursor handed to the
        # document reader is rejected. Steer instead of surfacing the bare error
        # (BL-145) — the reviewer lost a call to exactly this dead end.
        if "cursor" in message.lower():
            hint = (
                "cursors are not interchangeable between tools — a search_sentences cursor is "
                "not valid here. Page read_document_sentences with its own cursor, or address a "
                "slice directly with from_ordinal/to_ordinal."
            )
        raise ToolCallError(kind, message, hint=hint)
    data: dict[str, Any] = body["data"]
    return data


async def search_sentences(
    term: str,
    document_id: str | None = None,
    limit: int = 20,
    after: str | None = None,
    response_format: str = "detailed",
) -> dict[str, Any]:
    """Keyword-in-context search across the corpus you can read (pre/hit/post windows).

    **What it matches: a case-insensitive LITERAL SUBSTRING.** No stemming, no
    lemmatization, no word boundaries — and the rule is identical whether you pass
    ``document_id`` or search corpus-wide. Three consequences worth knowing before
    you read a result:

    - A short term matches *inside* longer words: ``Hick`` also returns ``thick``,
      ``thickness``. That is substring matching, not lemma normalization — narrow
      the term (``Hick's law``) rather than concluding the index mangled it.
    - ``%`` and ``_`` are literal characters here, not wildcards.
    - Typographic punctuation is folded, so a term typed with ``'`` also matches
      the ``’`` that PDF/EPUB extraction stores (likewise ``-``/``—`` and
      ``"``/``“”``). Spelling, hyphenation and spacing still have to match.

    Run ``term_frequency`` on your candidate terms FIRST (one batched call): it
    tells you which will flood before you spend a search on one.

    Use RARE, scene-specific terms. A common, high-frequency word floods one region
    of a document and starves coverage of the rest — the returned ``coverage`` and
    ``note`` tell you when that happened, so a sparse result reads as a *query*
    problem, not missing content. To reach untouched text, read the ordinal ranges
    beyond ``coverage.ordinal_max`` with ``read_document_sentences``.

    Returns ``{hits, coverage, note}``: ``hits`` is the keyword-in-context rows
    (pass the last row's ``cursor`` as ``after`` for the next page; oversized
    windows are clipped with an explicit ``… [+N chars]`` marker); ``coverage``
    reports the hit count and ordinal span; ``note`` is a steer (or ``null``)
    when the result may be flooded. ``limit`` is served up to the platform
    page-size maximum of 200 — a larger request is clamped, and the clamp is
    reported via ``coverage.requested_limit`` and the ``note``.

    ``response_format="concise"`` drops the row id, title, and per-row cursor
    (the last row keeps one, which is the only cursor paging uses), leaving the
    ordinal and the KWIC windows. ``coverage`` and ``note`` are unaffected.
    """
    _require_nonempty(term, "term")
    _require_uuid(document_id, "document_id")
    _require_response_format(response_format)
    concise = response_format == "concise"
    fields = (
        "documentId ordinal pre hit post cursor"
        if concise
        else ("id documentId title ordinal pre hit post cursor")
    )
    served = effective_limit(limit)
    data = await gql(
        "query($t:String!,$d:ID,$l:Int!,$a:String)"
        f"{{searchSentences(term:$t,documentId:$d,limit:$l,after:$a){{{fields}}}}}",
        {"t": term, "d": document_id, "l": served, "a": after},
    )
    hits = [_clip_windows(hit, ("pre", "hit", "post")) for hit in data["searchSentences"]]
    coverage, note = _search_coverage(
        hits,
        served,
        term=term,
        paged=after is not None,
        document_scoped=document_id is not None,
    )
    if served != limit:
        coverage["requested_limit"] = limit
        clamp_note = (
            f"requested limit {limit} exceeds the platform maximum {MAX_PAGE_SIZE}; "
            f"{served} hits per page were served."
        )
        note = clamp_note if note is None else f"{clamp_note} {note}"
    if concise:
        # A corpus-wide hit is unattributable without its document, so documentId
        # is only dropped when the caller already scoped the search to one.
        keep = ("ordinal", "pre", "hit", "post")
        hits = _project_concise(hits, keep if document_id else ("documentId", *keep))
    return {"hits": hits, "coverage": coverage, "note": note}


async def read_document_sentences(
    document_id: str,
    limit: int = 200,
    after: str | None = None,
    from_ordinal: int | None = None,
    to_ordinal: int | None = None,
    page: int | None = None,
    response_format: str = "detailed",
) -> list[dict[str, Any]]:
    """Read a document's sentences in order — sequential walk or a random-access slice.

    Unlike ``search_sentences`` (which returns only sentences matching a term),
    this returns every sentence in ``ordinal`` order. Two modes:

    - **Sequential (default):** page to exhaustion for 100% coverage — start with
      ``after=None``, pass the last row's ``cursor`` as ``after``, stop on ``[]``.
    - **Random access:** ``from_ordinal``/``to_ordinal`` (inclusive) jump straight
      to a slice (e.g. ``from_ordinal=4000, to_ordinal=4200``) without walking from
      the start; ``page`` reads one source page directly. These compose with
      ``limit``/``after`` for paging inside a range.

    Each sentence carries its citation handles — ``ordinal`` and the absolute
    ``charStart``/``charEnd`` offsets — so a claim drawn from any slice stays
    attributable to the same span whether read here or in the whole document. Text
    is returned **verbatim** (never clipped): payload is bounded by ``limit``, not
    by truncating content. Returns ``[]`` for an unknown or unreadable document.

    Use ``response_format="concise"`` for bulk reading: it drops the row id and
    the per-row cursor (the last row keeps one, which is the only cursor paging
    uses), leaving text plus its citation handles — measurably smaller per page,
    with attribution untouched. Aim it with ``read_document_outline``.
    """
    _require_uuid(document_id, "document_id")
    _require_response_format(response_format)
    if from_ordinal is not None and to_ordinal is not None and from_ordinal > to_ordinal:
        raise ToolCallError(
            "bad_input", f"from_ordinal ({from_ordinal}) must be <= to_ordinal ({to_ordinal})"
        )
    concise = response_format == "concise"
    fields = (
        "ordinal text charStart charEnd cursor"
        if concise
        else ("id ordinal text charStart charEnd cursor")
    )
    data = await gql(
        "query($id:ID!,$l:Int!,$a:String,$fo:Int,$to:Int,$pg:Int)"
        "{document(id:$id){sentences(limit:$l,after:$a,fromOrdinal:$fo,toOrdinal:$to,page:$pg)"
        f"{{{fields}}}}}}}",
        {
            "id": document_id,
            "l": effective_limit(limit),
            "a": after,
            "fo": from_ordinal,
            "to": to_ordinal,
            "pg": page,
        },
    )
    doc = data.get("document")
    if doc is None:
        return []
    sentences: list[dict[str, Any]] = doc["sentences"]
    if concise:
        return _project_concise(sentences, ("ordinal", "text", "charStart", "charEnd"))
    return sentences


async def full_text_search(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Rank whole DOCUMENTS by full-text relevance — the "which document?" tool.

    Use this to pick a document out of the corpus, not to find passages inside one:
    it returns one ranked row per document (``documentId``, ``title``, ``rank``),
    with no sentence text or citation handles. Once you have the document id, use
    ``search_sentences`` to locate specific passages, or ``read_document_sentences``
    with ``from_ordinal``/``to_ordinal`` to read a range verbatim.
    """
    _require_nonempty(query, "query")
    data = await gql(
        "query($q:String!,$l:Int!){fullTextSearch(query:$q,limit:$l){documentId title rank}}",
        {"q": query, "l": effective_limit(limit)},
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

    Pass the last row's ``cursor`` as ``after`` to fetch the next page (a
    non-last row's cursor re-serves the rows after it, which overlaps the page
    you already have). ``limit`` is served up to the platform page-size maximum
    of 200; a larger request gets the maximum.
    """
    data = await gql(
        "query($l:Int!,$dt:String,$s:String,$a:String)"
        "{documents(limit:$l,docType:$dt,status:$s,after:$a)"
        "{id title docType language status pageCount charCount sentenceCount createdAt cursor}}",
        {"l": effective_limit(limit), "dt": doc_type, "s": status, "a": after},
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
COLLECTION_FIELDS = (
    "id name description kind ruleSummary documentCount "
    "isScope parentLensId createdAt updatedAt cursor"
)


async def list_collections(
    limit: int = 50, kind: str | None = None, after: str | None = None
) -> list[dict[str, Any]]:
    """List document collections you can see (manual groupings and smart rules).

    Collections are doc-ray's native container for grouping documents — prefer
    them over ad-hoc tags. Pass ``kind`` to filter (e.g. ``manual``); pass the
    last row's ``cursor`` as ``after`` to page.
    """
    data = await gql(
        "query($l:Int!,$k:String,$a:String){collections(limit:$l,kind:$k,after:$a)"
        f"{{{COLLECTION_FIELDS}}}}}",
        {"l": effective_limit(limit), "k": kind, "a": after},
    )
    rows: list[dict[str, Any]] = data["collections"]
    return rows


async def list_lenses(limit: int = 50, after: str | None = None) -> list[dict[str, Any]]:
    """List the lenses — collections marked as entity-resolution scopes (Block B).

    A *lens* is a named scope under which a projection layer interprets entity
    identity: the fictional ``Duncan Idaho`` in one work stays distinct from an
    unrelated novel's Duncan, while the reserved ``global`` lens holds real-world
    (Wikidata-linkable) identities shared across the whole corpus. This is
    ``list_collections`` narrowed to ``isScope`` rows. Promoting a collection to a
    lens is a curation decision (``promote_collection_to_lens``), not a mechanical
    one. Pass the last row's ``cursor`` as ``after`` to page. ``documentCount``
    is scoped to the documents your token can see.
    """
    data = await gql(
        f"query($l:Int!,$a:String){{lenses(limit:$l,after:$a){{{COLLECTION_FIELDS}}}}}",
        {"l": effective_limit(limit), "a": after},
    )
    rows: list[dict[str, Any]] = data["lenses"]
    return rows


async def get_collection(collection_id: str, document_limit: int = 50) -> dict[str, Any] | None:
    """Fetch one collection with its member documents.

    Use it to work a curated set (a series, a source batch) as a unit: the member
    rows carry the document ids you then read or search individually. ``document_limit``
    bounds the members returned. Returns ``None`` when the collection is unknown or
    not visible to your token.
    """
    _require_uuid(collection_id, "collection_id")
    data = await gql(
        "query($id:ID!,$dl:Int!){collection(id:$id)"
        f"{{{COLLECTION_FIELDS} documents(limit:$dl){{id title docType status}}}}}}",
        {"id": collection_id, "dl": effective_limit(document_limit)},
    )
    collection: dict[str, Any] | None = data["collection"]
    return collection


def _require_shard(shard_index: int | None, shard_count: int) -> None:
    """Reject invalid shard arguments before any GraphQL round-trip."""
    if shard_count < 1:
        raise ToolCallError("bad_input", f"shard_count must be >= 1, got {shard_count}")
    if shard_index is not None and not 0 <= shard_index < shard_count:
        raise ToolCallError(
            "bad_input",
            f"shard_index must be in [0, {shard_count}), got {shard_index}",
        )


async def corpus_manifest(shard_count: int) -> dict[str, Any]:
    """Plan a corpus sweep: the shard map for splitting your corpus into N parts.

    Returns ``{shardCount, totalDocuments, shards:[{index, documentCount}]}`` for a
    deterministic ``hashtext(id) mod shardCount`` partition. Read this first to fan
    out one worker per shard, then pull each part with ``corpus_shard``; the
    per-shard counts sum to ``totalDocuments`` so you can verify full coverage.
    Scoped to the documents your token may read.
    """
    _require_shard(None, shard_count)
    data = await gql(
        "query($sc:Int!){corpusManifest(shardCount:$sc)"
        "{shardCount totalDocuments shards{index documentCount}}}",
        {"sc": shard_count},
    )
    manifest: dict[str, Any] = data["corpusManifest"]
    return manifest


async def corpus_shard(
    shard_index: int, shard_count: int, limit: int = 50, after: str | None = None
) -> list[dict[str, Any]]:
    """Read one shard of the corpus — a disjoint slice of documents for map-reduce.

    Returns the document summaries in shard ``shard_index`` of ``shard_count``
    (0-based), newest first. Page with the last row's ``cursor`` as ``after``
    to exhaust a shard; run shards ``0..shard_count-1`` (in parallel across
    agents) to cover the whole corpus with no overlap. Each row's ``id`` feeds
    ``read_document_sentences``/``get_document`` to pull that document's content.
    """
    _require_shard(shard_index, shard_count)
    data = await gql(
        "query($si:Int!,$sc:Int!,$l:Int!,$a:String)"
        "{corpusShard(shardIndex:$si,shardCount:$sc,limit:$l,after:$a)"
        "{id title docType status pageCount charCount createdAt cursor}}",
        {"si": shard_index, "sc": shard_count, "l": effective_limit(limit), "a": after},
    )
    docs: list[dict[str, Any]] = data["corpusShard"]
    return docs


async def get_document(document_id: str) -> dict[str, Any] | None:
    """Fetch one document's metadata and counts by id — the orientation call.

    Returns title, type, language, status, page/char counts and sentence count, but
    NO text: use it to size a document before reading it (the sentence count is the
    upper bound for ``read_document_sentences`` ordinals). Returns ``None`` when the
    id is unknown or not visible to your token.
    """
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
    """Most frequent named entities across the corpus, or within one document.

    Good for structure and cast — who and what a document is about — before reading
    it. Scope with ``document_id`` and narrow with ``entity_type``. Note the counts
    come from automated NER: the same real-world entity can appear under more than
    one type, so treat the rows as evidence to corroborate (with ``search_sentences``
    or ``lexeme_kwic``), not as a settled roster.
    """
    _require_uuid(document_id, "document_id")
    data = await gql(
        "query($d:ID,$e:String,$l:Int!){entityFrequency(documentId:$d,entityType:$e,limit:$l)"
        "{text entityType count}}",
        {"d": document_id, "e": entity_type, "l": effective_limit(limit)},
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
        {"u": uid, "d": document_id, "l": effective_limit(limit)},
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
        {"d": document_id, "l": effective_limit(limit), "a": after},
    )
    rows: list[dict[str, Any]] = data["documentImages"]
    return rows


async def image_class_summary(document_id: str) -> list[dict[str, Any]]:
    """The ``(imageClass, count)`` distribution of a document's detected images.

    A cheap shape-of-the-visuals call — how many diagrams vs photos vs tables were
    detected — to decide whether the visual layer is worth querying at all before
    spending calls on ``document_images`` or ``visual_captions``.
    """
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
        {"q": query, "l": effective_limit(limit)},
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
        {"d": document_id, "l": effective_limit(limit)},
    )
    rows: list[dict[str, Any]] = data["visualCaptions"]
    return rows


async def entity_mention_corrections(document_id: str) -> list[dict[str, Any]]:
    """A document's durable human NER corrections, oldest first (the audit log).

    Each row shows the corrective ``action`` (reclassify / reject / add /
    adjust_span), the target span (``charStart`` / ``charEnd``), the corrected
    ``entityType`` (and ``newCharStart`` / ``newCharEnd`` for adjust_span), what
    the model originally said (``originalType``), and the reviewer's
    ``rationale``. Use it to see what has already been fixed before recording a
    new ``correct_entity_mention``. RLS-scoped to documents your token may read.
    """
    _require_uuid(document_id, "document_id")
    data = await gql(
        "query($d:ID!){entityMentionCorrections(documentId:$d)"
        "{action charStart charEnd entityType surface newCharStart newCharEnd "
        "originalType rationale createdAt}}",
        {"d": document_id},
    )
    rows: list[dict[str, Any]] = data["entityMentionCorrections"]
    return rows


from doc_ray.entrypoints.mcp.visual_review import visual_review  # noqa: E402

#: The read tools registered on the MCP server (see server.py).
TOOLS: tuple[Callable[..., Any], ...] = (
    search_sentences,
    read_document_sentences,
    full_text_search,
    list_documents,
    get_document,
    get_job,
    list_collections,
    get_collection,
    list_lenses,
    corpus_manifest,
    corpus_shard,
    entity_frequency,
    lexeme_kwic,
    document_images,
    image_class_summary,
    cross_modal_image_search,
    visual_captions,
    visual_review,
    entity_mention_corrections,
)
