"""MCP resource layer — ``docray://`` URIs and RFC 6570 templates over the read tools.

Resources are the *addressing* primitive: a host can enumerate them
(``resources/templates/list``) and read one by URI (``resources/read``) instead of
composing a tool call. They add no capability and no privilege — each read goes
through the same :mod:`~doc_ray.entrypoints.mcp.corpus` functions, so the caller's
bearer token and Postgres RLS scope the result exactly as they do for a tool, and
``resources/read`` still lands its bytes in the caller's context (the resource
abstraction improves addressing and host-side caching, not payload size).

The ranged template (``…/sentences/{start}-{end}``) is the one that matters for
large documents: it is a bounded, in-order, citable slice. The whole-document URI
is a convenience that truncates **explicitly** rather than silently, pointing at
the ranged form for the remainder.

SDK-independent by design (like ``corpus.py``): these are plain async functions
returning text; :mod:`~doc_ray.entrypoints.mcp.server` binds them to URIs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from doc_ray.entrypoints.mcp import corpus

#: Hard cap on a single whole-document resource read. Beyond this the text is cut
#: with an explicit marker naming the ranged template — truncation is never silent.
MAX_RESOURCE_TEXT_CHARS = 200_000

#: Sentences fetched per page while walking a document for the full-text resource.
_WALK_PAGE = 500

#: Rows returned by the listable corpus entry points.
_INDEX_LIMIT = 100

#: Shard width served by the parameterless ``docray://corpus/manifest`` entry
#: point; other widths stay available via the ``corpus/shards/{n}`` template.
DEFAULT_SHARD_COUNT = 8


@dataclass(frozen=True)
class ResourceSpec:
    """One ``docray://`` URI (or template) bound to the function that serves it."""

    uri: str
    fn: Callable[..., Any]
    mime_type: str
    description: str


def _join(rows: list[dict[str, Any]]) -> str:
    """Render sentence rows as one line per sentence, in ordinal order."""
    return "\n".join(str(row.get("text", "")) for row in rows)


async def document_full_text(document_id: str) -> str:
    """The document's full text, one sentence per line, in ordinal order.

    Walks the sequential read to exhaustion, then truncates at
    :data:`MAX_RESOURCE_TEXT_CHARS` with an explicit marker pointing at the ranged
    template — a resource read cannot exceed the caller's context any more than a
    tool result can, so the cut is made visible rather than silent.
    """
    parts: list[str] = []
    total = 0
    after: str | None = None
    while total < MAX_RESOURCE_TEXT_CHARS:
        page = await corpus.read_document_sentences(document_id, limit=_WALK_PAGE, after=after)
        if not page:
            break
        parts.extend(str(row.get("text", "")) for row in page)
        total = sum(len(p) + 1 for p in parts)
        after = page[-1].get("cursor")
        if after is None:
            break
    text = "\n".join(parts)
    if len(text) > MAX_RESOURCE_TEXT_CHARS:
        text = (
            text[:MAX_RESOURCE_TEXT_CHARS]
            + f"\n… [truncated at {MAX_RESOURCE_TEXT_CHARS} chars — read the remainder in "
            f"bounded slices via docray://doc/{document_id}/sentences/{{start}}-{{end}}]"
        )
    return text


async def document_sentence_range(document_id: str, start: int, end: int) -> str:
    """Sentences ``start``..``end`` (inclusive ordinals), one per line.

    The bounded, random-access slice — no walking from the start. Every sentence
    keeps its ordinal position, so the slice stays citable against the whole text.
    """
    rows = await corpus.read_document_sentences(
        document_id, limit=_WALK_PAGE, from_ordinal=start, to_ordinal=end
    )
    return _join(rows)


async def document_page(document_id: str, page: int) -> str:
    """Every sentence on one source page, in ordinal order."""
    rows = await corpus.read_document_sentences(document_id, limit=_WALK_PAGE, page=page)
    return _join(rows)


async def collection_document(collection_id: str) -> str:
    """One collection and its member documents, as JSON."""
    collection = await corpus.get_collection(collection_id)
    return json.dumps(collection or {}, indent=2, default=str)


async def corpus_shard_manifest(shard_count: int) -> str:
    """The corpus shard map for ``shard_count`` parts, as JSON.

    The plan for a map-reduce sweep: per-shard document counts that sum to the
    total, so an orchestrator can verify it covered everything.
    """
    manifest = await corpus.corpus_manifest(shard_count)
    return json.dumps(manifest, indent=2, default=str)


async def corpus_documents() -> str:
    """The document index — id, title, type and counts — as JSON.

    A **listable entry point**: parameterless, so it appears in ``resources/list``
    and gives a client something to enumerate and a caller somewhere to start.
    Drill into a document with the ``docray://doc/{document_id}`` templates.
    """
    docs = await corpus.list_documents(limit=_INDEX_LIMIT)
    return json.dumps(docs, indent=2, default=str)


async def corpus_collections() -> str:
    """The collection index — doc-ray's native containers — as JSON."""
    collections = await corpus.list_collections(limit=_INDEX_LIMIT)
    return json.dumps(collections, indent=2, default=str)


async def corpus_default_manifest() -> str:
    """The corpus shard map at the default width (:data:`DEFAULT_SHARD_COUNT`), as JSON.

    The parameterless companion to ``docray://corpus/shards/{shard_count}`` so the
    map-reduce plan is reachable without knowing a width up front.
    """
    manifest = await corpus.corpus_manifest(DEFAULT_SHARD_COUNT)
    return json.dumps(manifest, indent=2, default=str)


#: Every ``docray://`` surface, bound to its server function. Registered
#: unconditionally (all reads, RLS-scoped) by ``server.build_server``.
#:
#: The first three are **concrete** (no ``{param}``): the SDK keeps concrete and
#: templated resources in *disjoint* registries, and ``resources/list`` returns
#: only the concrete ones — so a template-only surface is invisible to any client
#: that enumerates ``resources/list``. These are the listable entry points; the
#: templates below are drill-down targets reached from them.
RESOURCES: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        "docray://corpus/documents",
        corpus_documents,
        "application/json",
        "The document index (id, title, type, counts) — start here.",
    ),
    ResourceSpec(
        "docray://corpus/collections",
        corpus_collections,
        "application/json",
        "The collection index — doc-ray's native document containers.",
    ),
    ResourceSpec(
        "docray://corpus/manifest",
        corpus_default_manifest,
        "application/json",
        f"Corpus shard map at the default width ({DEFAULT_SHARD_COUNT} shards).",
    ),
    ResourceSpec(
        "docray://doc/{document_id}",
        document_full_text,
        "text/plain",
        "Full text of one document, one sentence per line; truncates explicitly.",
    ),
    ResourceSpec(
        "docray://doc/{document_id}/sentences/{start}-{end}",
        document_sentence_range,
        "text/plain",
        "Sentences start..end (inclusive ordinals) — a bounded, citable slice.",
    ),
    ResourceSpec(
        "docray://doc/{document_id}/pages/{page}",
        document_page,
        "text/plain",
        "Every sentence on one source page of a document.",
    ),
    ResourceSpec(
        "docray://collection/{collection_id}",
        collection_document,
        "application/json",
        "One collection and its member documents, as JSON.",
    ),
    ResourceSpec(
        "docray://corpus/shards/{shard_count}",
        corpus_shard_manifest,
        "application/json",
        "Corpus shard map for a map-reduce sweep, as JSON.",
    ),
)
