"""MCP write tools — reprocessing + curation mutations, forwarded to the GraphQL API.

Each tool is a thin wrapper over the corpus GraphQL passthrough (``corpus.gql``):
it validates arguments locally, sends the exact mutation the API already exposes
([entrypoints/api/mutation.py]), forwards the caller's bearer token, and returns
the ``Job`` payload (or the scalar the mutation returns).

Authorization is NOT re-implemented here. The API's guards are authoritative for
every write — ``_require_write_audited`` (authenticated principal + ``write``
scope + billing entitlement, with permission-denied audit events) and
``_require_document_write`` (per-document ACL grant, BL-064). A caller lacking the
scope or grant receives the API's ``permission_denied`` mapped to a typed
:class:`~doc_ray.entrypoints.mcp.corpus.ToolCallError`, exactly as reads are.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — these tools are invoked by LLM agents.
The MCP layer's contribution to write safety is twofold and deliberately narrow:
(1) these tools are registered only when ``DOCRAY_MCP_ENABLE_WRITE`` is set, so the
default surface is provably read-only; and (2) only reprocessing and curation
mutations are exposed — destructive/admin verbs (delete, archive, backup/restore,
cancel_job) are intentionally absent. Every mutation here enqueues a reversible
background job or a curation decision; none deletes data.

Callers poll progress with the ``get_job`` read tool
(:func:`~doc_ray.entrypoints.mcp.corpus.get_job`).
"""

from __future__ import annotations

from typing import Any

from doc_ray.entrypoints.mcp.corpus import (
    JOB_FIELDS,
    ToolCallError,
    _require_uuid,
    gql,
)

#: A single-document mutation returning a Job, parameterised by GraphQL field name
#: and any extra scalar arguments beyond ``documentId``.


async def _job_mutation(
    field: str, extra_decl: str, extra_call: str, variables: dict[str, Any]
) -> dict[str, Any]:
    """Send a ``mutation`` selecting :data:`JOB_FIELDS` and return the Job payload."""
    query = f"mutation($id:ID!{extra_decl}){{{field}(documentId:$id{extra_call}){{{JOB_FIELDS}}}}}"
    data = await gql(query, variables)
    job: dict[str, Any] = data[field]
    return job


async def reannotate(document_id: str, lang: str = "en") -> dict[str, Any]:
    """Re-run the full linguistic annotation for a document; returns the queued job."""
    _require_uuid(document_id, "document_id")
    return await _job_mutation(
        "reannotate", ",$lang:String!", ",lang:$lang", {"id": document_id, "lang": lang}
    )


async def reextract_document(document_id: str) -> dict[str, Any]:
    """Backfill PDF page geometry for one document; returns the queued job."""
    _require_uuid(document_id, "document_id")
    return await _job_mutation("reextractDocument", "", "", {"id": document_id})


async def annotate_document(document_id: str, lang: str = "en") -> dict[str, Any]:
    """Run Stage-1 annotation (tokens / NER / coref) for a document; returns the job."""
    _require_uuid(document_id, "document_id")
    return await _job_mutation(
        "annotateDocument", ",$lang:String!", ",lang:$lang", {"id": document_id, "lang": lang}
    )


async def semantic_enrich_document(document_id: str) -> dict[str, Any]:
    """Run deterministic semantic enrichment for a document; returns the job."""
    _require_uuid(document_id, "document_id")
    return await _job_mutation("semanticEnrichDocument", "", "", {"id": document_id})


async def canonicalize_document(document_id: str, resolve: bool = False) -> dict[str, Any]:
    """Canonicalize a document (term scan + Stage-2 auto-link); returns the job.

    ``resolve=True`` additionally runs the frontier LLM resolver on the residue —
    model cost is always an explicit request, never a default (D-018), so this
    defaults to ``False``.
    """
    _require_uuid(document_id, "document_id")
    return await _job_mutation(
        "canonicalizeDocument", ",$r:Boolean!", ",resolve:$r", {"id": document_id, "r": resolve}
    )


async def suggest_seeds(document_id: str, limit: int = 40) -> dict[str, Any]:
    """Mine + KB-resolve seed candidates for a document into pending suggestions.

    Nothing reaches the canonical registry without a subsequent ``decide_seeds``
    approval; this only populates ``seedSuggestions``. Returns the queued job.
    """
    _require_uuid(document_id, "document_id")
    return await _job_mutation(
        "suggestSeeds", ",$l:Int!", ",limit:$l", {"id": document_id, "l": limit}
    )


async def suggest_document_metadata(document_id: str) -> dict[str, Any]:
    """Generate verified title/tag/duplicate suggestions for a document; returns the job."""
    _require_uuid(document_id, "document_id")
    return await _job_mutation("suggestDocumentMetadata", "", "", {"id": document_id})


async def seed_approved(document_id: str) -> dict[str, Any]:
    """Seed a document's approved suggestions into the canonical registry; returns the job."""
    _require_uuid(document_id, "document_id")
    return await _job_mutation("seedApproved", "", "", {"id": document_id})


async def update_document_metadata(
    document_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Edit a document's title/description/tags; returns the queued job.

    Only the fields you pass change; each changed field is recorded in the
    document's metadata history. ``tags``, when passed, fully replaces the current
    tag set. Requires a write grant on the document (BL-064).
    """
    _require_uuid(document_id, "document_id")
    query = (
        "mutation($id:ID!,$t:String,$d:String,$tags:[String!])"
        "{updateDocumentMetadata(documentId:$id,title:$t,description:$d,tags:$tags)"
        f"{{{JOB_FIELDS}}}}}"
    )
    data = await gql(query, {"id": document_id, "t": title, "d": description, "tags": tags})
    job: dict[str, Any] = data["updateDocumentMetadata"]
    return job


async def bulk_tag_documents(document_ids: list[str], tags: list[str]) -> list[dict[str, Any]]:
    """Merge ``tags`` into each of ``document_ids``; returns one queued job per document."""
    clean_tags = sorted({tag.strip() for tag in tags if tag and tag.strip()})
    if not clean_tags:
        raise ToolCallError("bad_input", "at least one non-blank tag is required")
    ids = [_require_uuid(d, "document_ids") for d in document_ids]
    query = (
        "mutation($ids:[ID!]!,$tags:[String!]!)"
        f"{{bulkTagDocuments(documentIds:$ids,tags:$tags){{{JOB_FIELDS}}}}}"
    )
    data = await gql(query, {"ids": ids, "tags": clean_tags})
    jobs: list[dict[str, Any]] = data["bulkTagDocuments"]
    return jobs


async def decide_seeds(
    approve_ids: list[str] | None = None, reject_ids: list[str] | None = None
) -> int:
    """Approve/reject pending seed suggestions; returns the number of rows updated.

    ``seeded`` rows are immutable — re-deciding them is a no-op.
    """
    data = await gql(
        "mutation($a:[ID!],$r:[ID!]){decideSeeds(approveIds:$a,rejectIds:$r)}",
        {"a": approve_ids, "r": reject_ids},
    )
    updated: int = data["decideSeeds"]
    return updated


async def decide_document_metadata_suggestions(
    approve_ids: list[str] | None = None, reject_ids: list[str] | None = None
) -> int:
    """Approve/reject verified metadata suggestions (without auto-applying); rows updated."""
    data = await gql(
        "mutation($a:[ID!],$r:[ID!])"
        "{decideDocumentMetadataSuggestions(approveIds:$a,rejectIds:$r)}",
        {"a": approve_ids, "r": reject_ids},
    )
    updated: int = data["decideDocumentMetadataSuggestions"]
    return updated


#: The write tools registered when ``DOCRAY_MCP_ENABLE_WRITE`` is set. Reprocessing
#: (reannotate … suggest_document_metadata) and curation (update_document_metadata,
#: bulk_tag_documents, decide_*, seed_approved) only — no destructive/admin verbs.
TOOLS: tuple[Any, ...] = (
    reannotate,
    reextract_document,
    annotate_document,
    semantic_enrich_document,
    canonicalize_document,
    suggest_seeds,
    suggest_document_metadata,
    update_document_metadata,
    bulk_tag_documents,
    decide_seeds,
    decide_document_metadata_suggestions,
    seed_approved,
)
