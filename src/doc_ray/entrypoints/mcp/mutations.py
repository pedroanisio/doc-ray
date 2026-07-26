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
    COLLECTION_FIELDS,
    JOB_FIELDS,
    ToolCallError,
    _require_nonempty,
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


async def embed_document_sentences(document_id: str) -> dict[str, Any]:
    """Embed a document's sentences with the learned encoder (NLP Layer 20); returns the job.

    Heavy, worker-only: requires the sentence-transformers stack. Never
    auto-chained. Once complete, ``semantic_search_sentences`` retrieves against
    these vectors. Fails typed-and-actionable if the stack is not installed.
    """
    _require_uuid(document_id, "document_id")
    return await _job_mutation("embedDocumentSentences", "", "", {"id": document_id})


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
    authors: list[str] | None = None,
) -> dict[str, Any]:
    """Edit a document's title/description/tags/authors; returns the queued job.

    Only the fields you pass change; each changed field is recorded in the
    document's metadata history. ``tags`` (a set) and ``authors`` (an ordered
    list — author order is kept) each fully replace the current set when passed;
    pass ``authors=[]`` to clear them. Requires a write grant on the document
    (BL-064).
    """
    _require_uuid(document_id, "document_id")
    query = (
        "mutation($id:ID!,$t:String,$d:String,$tags:[String!],$authors:[String!])"
        "{updateDocumentMetadata(documentId:$id,title:$t,description:$d,tags:$tags,authors:$authors)"
        f"{{{JOB_FIELDS}}}}}"
    )
    data = await gql(
        query,
        {"id": document_id, "t": title, "d": description, "tags": tags, "authors": authors},
    )
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


async def correct_entity_mention(
    document_id: str,
    action: str,
    char_start: int,
    char_end: int,
    entity_type: str | None = None,
    surface: str | None = None,
    new_char_start: int | None = None,
    new_char_end: int | None = None,
    original_type: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    """Record a durable human fix to an ML-produced NER mention; returns the queued job.

    ``action`` is one of:

    * ``reclassify`` — the span is a real entity of a different type; pass the
      correct ``entity_type`` (e.g. a mention typed ``PERSON`` that is really a
      ``GPE``).
    * ``reject`` — the span is not an entity; it is dropped.
    * ``add`` — a missed entity; pass its ``entity_type`` (and ``surface``).
    * ``adjust_span`` — the entity is real but its boundaries are wrong; pass the
      corrected ``new_char_start`` / ``new_char_end``.

    ``char_start`` / ``char_end`` identify the target span by document character
    offsets — the stable key the correction is stored under, so the fix survives
    every future re-annotation (``entity_mention`` is otherwise rebuilt from
    scratch each run). ``original_type`` and ``rationale`` are recorded for the
    audit trail (PALS's Law: fixes are attributable and reversible). A cheap
    ``apply_corrections`` job re-applies the log onto the current mentions; poll
    it with ``get_job``. Read the log back with ``entity_mention_corrections``.
    Requires a write grant on the document (BL-064).
    """
    _require_uuid(document_id, "document_id")
    _require_nonempty(action, "action")
    query = (
        "mutation($id:ID!,$a:String!,$cs:Int!,$ce:Int!,$et:String,$sf:String,"
        "$ncs:Int,$nce:Int,$ot:String,$r:String)"
        "{correctEntityMention(documentId:$id,action:$a,charStart:$cs,charEnd:$ce,"
        "entityType:$et,surface:$sf,newCharStart:$ncs,newCharEnd:$nce,"
        f"originalType:$ot,rationale:$r){{{JOB_FIELDS}}}}}"
    )
    data = await gql(
        query,
        {
            "id": document_id,
            "a": action,
            "cs": char_start,
            "ce": char_end,
            "et": entity_type,
            "sf": surface,
            "ncs": new_char_start,
            "nce": new_char_end,
            "ot": original_type,
            "r": rationale,
        },
    )
    job: dict[str, Any] = data["correctEntityMention"]
    return job


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


async def decide_visual_captions(
    approve_ids: list[str] | None = None, reject_ids: list[str] | None = None
) -> int:
    """Commit or reject untrusted Tier-3 caption proposals after review."""
    data = await gql(
        "mutation($a:[ID!],$r:[ID!]){decideVisualCaptions(approveIds:$a,rejectIds:$r)}",
        {"a": approve_ids, "r": reject_ids},
    )
    updated: int = data["decideVisualCaptions"]
    return updated


async def create_collection(
    name: str,
    description: str = "",
    kind: str = "manual",
    rule: dict[str, Any] | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Create a document collection — doc-ray's native container for grouping docs.

    Prefer this over ad-hoc tags when the intent is a named, browsable group. The
    default ``manual`` kind is a hand-curated set you fill with
    ``add_document_to_collection``; a ``smart`` kind takes a ``rule`` (JSON) plus
    optional ``doc_type``/``status``/``tags`` filters that auto-populate it.
    Returns the created collection.
    """
    _require_nonempty(name, "name")
    query = (
        "mutation($n:String!,$d:String!,$k:String!,$r:JSON,$dt:String,$s:String,$t:[String!])"
        "{createCollection(name:$n,description:$d,kind:$k,rule:$r,docType:$dt,status:$s,tags:$t)"
        f"{{{COLLECTION_FIELDS}}}}}"
    )
    data = await gql(
        query,
        {"n": name, "d": description, "k": kind, "r": rule, "dt": doc_type, "s": status, "t": tags},
    )
    collection: dict[str, Any] = data["createCollection"]
    return collection


async def add_document_to_collection(collection_id: str, document_id: str) -> dict[str, Any]:
    """Add a document to a manual collection; returns the updated collection.

    Requires a write grant on the document and a ``manual`` (not smart-rule)
    collection — the API rejects adding to a rule-driven collection.
    """
    _require_uuid(collection_id, "collection_id")
    _require_uuid(document_id, "document_id")
    data = await gql(
        "mutation($c:ID!,$d:ID!){addDocumentToCollection(collectionId:$c,documentId:$d)"
        f"{{{COLLECTION_FIELDS}}}}}",
        {"c": collection_id, "d": document_id},
    )
    collection: dict[str, Any] = data["addDocumentToCollection"]
    return collection


async def remove_document_from_collection(collection_id: str, document_id: str) -> dict[str, Any]:
    """Remove a document from a manual collection; returns the updated collection."""
    _require_uuid(collection_id, "collection_id")
    _require_uuid(document_id, "document_id")
    data = await gql(
        "mutation($c:ID!,$d:ID!){removeDocumentFromCollection(collectionId:$c,documentId:$d)"
        f"{{{COLLECTION_FIELDS}}}}}",
        {"c": collection_id, "d": document_id},
    )
    collection: dict[str, Any] = data["removeDocumentFromCollection"]
    return collection


async def update_collection(
    collection_id: str, name: str | None = None, description: str | None = None
) -> dict[str, Any]:
    """Rename a collection and/or edit its description; returns the updated collection.

    Only the fields you pass change (use this to fix a bad name, e.g. an
    HTML-escaped one). Renaming to a name another collection already holds is a
    clean error — names are globally unique.
    """
    _require_uuid(collection_id, "collection_id")
    data = await gql(
        "mutation($c:ID!,$n:String,$d:String){updateCollection(collectionId:$c,name:$n,description:$d)"
        f"{{{COLLECTION_FIELDS}}}}}",
        {"c": collection_id, "n": name, "d": description},
    )
    collection: dict[str, Any] = data["updateCollection"]
    return collection


async def delete_collection(collection_id: str) -> bool:
    """Delete a collection; returns True. Removes only the container.

    The member documents are untouched — only the grouping is removed. Use it to
    clean up a mistaken collection.
    """
    _require_uuid(collection_id, "collection_id")
    data = await gql(
        "mutation($c:ID!){deleteCollection(collectionId:$c)}",
        {"c": collection_id},
    )
    deleted: bool = data["deleteCollection"]
    return deleted


async def promote_collection_to_lens(
    collection_id: str, parent_lens_id: str | None = None
) -> dict[str, Any]:
    """Promote a collection to a *lens* — a named entity-resolution scope (Block B).

    A lens is the scope under which a projection layer will later interpret entity
    identity, so a fictional ``Duncan Idaho`` in one work stays distinct from an
    unrelated novel's Duncan. This is a **curation decision**, not a mechanical
    step: mark a collection a lens only when its documents share an identity world
    (a series, a franchise, one novel). Optionally nest it under ``parent_lens_id``,
    which must already be a lens. Nothing about how entities resolve changes today;
    this only names the scope. Returns the updated collection (``isScope`` true).
    """
    _require_uuid(collection_id, "collection_id")
    if parent_lens_id is not None:
        _require_uuid(parent_lens_id, "parent_lens_id")
    data = await gql(
        "mutation($c:ID!,$p:ID){promoteCollectionToLens(collectionId:$c,parentLensId:$p)"
        f"{{{COLLECTION_FIELDS}}}}}",
        {"c": collection_id, "p": parent_lens_id},
    )
    collection: dict[str, Any] = data["promoteCollectionToLens"]
    return collection


async def demote_lens(collection_id: str) -> dict[str, Any]:
    """Demote a lens back to an ordinary collection (clears ``isScope``).

    Refuses for the reserved ``global`` lens, while any document still names it as
    its default lens, and while it still parents another lens — reassign or demote
    those first. The collection and its membership are untouched. Returns the
    updated collection.
    """
    _require_uuid(collection_id, "collection_id")
    data = await gql(
        f"mutation($c:ID!){{demoteLens(collectionId:$c){{{COLLECTION_FIELDS}}}}}",
        {"c": collection_id},
    )
    collection: dict[str, Any] = data["demoteLens"]
    return collection


async def set_document_default_lens(document_id: str, lens_id: str | None = None) -> dict[str, Any]:
    """Set (or clear) a document's default lens (Block B).

    The lens must be a scope collection the document already belongs to; pass a
    null ``lens_id`` to clear the default. Requires a write grant on the document.
    Returns the updated document (its ``defaultLensId`` reflects the change).
    """
    _require_uuid(document_id, "document_id")
    if lens_id is not None:
        _require_uuid(lens_id, "lens_id")
    data = await gql(
        "mutation($d:ID!,$l:ID){setDocumentDefaultLens(documentId:$d,lensId:$l)"
        "{id title defaultLensId}}",
        {"d": document_id, "l": lens_id},
    )
    document: dict[str, Any] = data["setDocumentDefaultLens"]
    return document


#: The write tools registered when ``DOCRAY_MCP_ENABLE_WRITE`` is set. Reprocessing
#: (reannotate … suggest_document_metadata), curation (update_document_metadata,
#: bulk_tag_documents, decide_*, seed_approved), and collection management
#: (create/add/remove) only — no destructive/admin verbs.
async def classify_document(document_id: str) -> dict[str, Any]:
    """Enqueue a heuristic genre classification for a document (SLP Block C); returns the job.

    The worker reads the document's text and writes a **pending** machine genre
    guess (fiction / non_fiction / mixed / unknown) to its classification, using
    deterministic signals — no model. A pending result is a GUESS, not a decided
    genre: read it with ``document_classification`` and make it authoritative with
    ``confirm_document_classification``. The job never overwrites a human-confirmed
    classification. Poll it with ``get_job``. Requires a write grant on the
    document (BL-064).
    """
    _require_uuid(document_id, "document_id")
    data = await gql(
        f"mutation($id:ID!){{classifyDocument(documentId:$id){{{JOB_FIELDS}}}}}",
        {"id": document_id},
    )
    job: dict[str, Any] = data["classifyDocument"]
    return job


async def confirm_document_classification(
    document_id: str,
    genre: str,
    subtype: str | None = None,
    rationale: str | None = None,
) -> dict[str, Any]:
    """Confirm a document's genre as a human decision (SLP Block C); returns the classification.

    This is the verification step that turns a machine guess into a decided genre:
    it records ``status='confirmed'``, ``method='human'``, the reviewer, and your
    ``rationale``, and overrides any prior value. ``genre`` must be one of
    ``fiction``, ``non_fiction``, ``mixed``, ``unknown``; ``subtype`` is an optional
    finer label (e.g. ``novel``, ``biography``, ``paper``). Use this only when you
    have actually judged the document — a confirmed genre feeds identity-resolution
    policy downstream. Requires a write grant on the document (BL-064).
    """
    _require_uuid(document_id, "document_id")
    _require_nonempty(genre, "genre")
    query = (
        "mutation($id:ID!,$g:String!,$st:String,$r:String)"
        "{confirmDocumentClassification(documentId:$id,genre:$g,subtype:$st,rationale:$r)"
        "{documentId genre subtype confidence method status reviewer rationale "
        "createdAt updatedAt}}"
    )
    data = await gql(query, {"id": document_id, "g": genre, "st": subtype, "r": rationale})
    classification: dict[str, Any] = data["confirmDocumentClassification"]
    return classification


TOOLS: tuple[Any, ...] = (
    reannotate,
    reextract_document,
    annotate_document,
    semantic_enrich_document,
    embed_document_sentences,
    canonicalize_document,
    suggest_seeds,
    suggest_document_metadata,
    update_document_metadata,
    bulk_tag_documents,
    correct_entity_mention,
    classify_document,
    confirm_document_classification,
    decide_seeds,
    decide_document_metadata_suggestions,
    decide_visual_captions,
    seed_approved,
    create_collection,
    add_document_to_collection,
    remove_document_from_collection,
    update_collection,
    delete_collection,
    promote_collection_to_lens,
    demote_lens,
    set_document_default_lens,
)
