"""Dry-run-first MCP write for exact-surface NER correction rules.

The tool resolves one document or one bounded collection into an explicit list
of document ids, previews exact ``(surface, current type)`` occurrences, and only
writes when ``dry_run=False`` is stated. GraphQL remains the authorization and
audit boundary; this layer prevents ambiguous or silently truncated scope.
"""

from __future__ import annotations

from typing import Any

from doc_ray.entrypoints.mcp.corpus import (
    JOB_FIELDS,
    ToolCallError,
    _require_nonempty,
    _require_uuid,
    get_collection,
    gql,
)

COLLECTION_DOCUMENT_LIMIT = 200
_ACTIONS = frozenset({"reclassify_surface", "reject_surface"})

_PREVIEW = (
    "query($ids:[ID!]!,$s:String!,$ot:String!)"
    "{entitySurfaceOccurrences(documentIds:$ids,surface:$s,originalType:$ot)"
    "{documentId surface originalType occurrenceCount countCap countCapped "
    "samples{mentionId charStart charEnd sentenceOrdinal sentenceContext}}}"
)


def _validate_rule(surface: str, original_type: str, action: str, entity_type: str | None) -> None:
    """Reject a malformed exact-surface rule before any GraphQL call."""
    _require_nonempty(surface, "surface")
    _require_nonempty(original_type, "original_type")
    _require_nonempty(action, "action")
    if action not in _ACTIONS:
        raise ToolCallError(
            "bad_input",
            "action must be one of reclassify_surface, reject_surface",
        )
    if action == "reclassify_surface":
        if entity_type is None or not entity_type.strip():
            raise ToolCallError("bad_input", "reclassify_surface requires entity_type")
        if entity_type == original_type:
            raise ToolCallError(
                "bad_input", "reclassify_surface entity_type must differ from original_type"
            )


async def _collection_document_ids(collection_id: str) -> list[str]:
    """Resolve a collection only when the bounded member page proves completeness."""
    collection = await get_collection(collection_id, document_limit=COLLECTION_DOCUMENT_LIMIT)
    if collection is None:
        raise ToolCallError("bad_input", f"collection {collection_id} is unknown or not visible")
    documents = collection.get("documents")
    if not isinstance(documents, list) or not documents:
        raise ToolCallError("bad_input", "collection has no visible documents")
    declared_count = collection.get("documentCount")
    if (
        len(documents) >= COLLECTION_DOCUMENT_LIMIT
        or not isinstance(declared_count, int)
        or declared_count != len(documents)
    ):
        raise ToolCallError(
            "bad_input",
            "collection membership is not provably complete within the 200-document bound",
            hint="pass one document_id, or split the collection below 200 members",
        )
    ids = [_require_uuid(row.get("id"), "collection document id") for row in documents]
    if any(document_id is None for document_id in ids):
        raise ToolCallError("bad_input", "collection returned a document without an id")
    unique = list(dict.fromkeys(str(document_id) for document_id in ids))
    if len(unique) != len(documents):
        raise ToolCallError("bad_input", "collection returned duplicate document ids")
    return unique


async def _scope(
    document_id: str | None, collection_id: str | None
) -> tuple[list[str], dict[str, Any]]:
    """Return explicit document ids and an honest description of their source."""
    if (document_id is None) == (collection_id is None):
        raise ToolCallError("bad_input", "pass exactly one of document_id or collection_id")
    if document_id is not None:
        checked = _require_uuid(document_id, "document_id")
        assert checked is not None
        return [checked], {"kind": "document", "id": checked, "documentCount": 1}
    checked_collection = _require_uuid(collection_id, "collection_id")
    assert checked_collection is not None
    ids = await _collection_document_ids(checked_collection)
    return ids, {
        "kind": "collection",
        "id": checked_collection,
        "documentCount": len(ids),
    }


async def correct_entity_surface(
    surface: str,
    original_type: str,
    action: str,
    document_id: str | None = None,
    collection_id: str | None = None,
    entity_type: str | None = None,
    rationale: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Preview or apply an exact surface/current-type NER correction rule.

    Pass exactly one ``document_id`` or ``collection_id``. Collection membership
    must be provably complete below the 200-document bound; the tool refuses an
    ambiguous page instead of silently applying to only its first members.

    ``action`` is ``reclassify_surface`` (requires a distinct ``entity_type``) or
    ``reject_surface``. Matching is exact and case-sensitive, never fuzzy, regex,
    or corpus-global. The default ``dry_run=True`` performs only the bounded
    occurrence preview. Set ``dry_run=False`` explicitly to append the durable
    rule and enqueue ``apply_corrections`` for every authorized document.
    """
    _validate_rule(surface, original_type, action, entity_type)
    document_ids, scope = await _scope(document_id, collection_id)
    preview_data = await gql(
        _PREVIEW,
        {"ids": document_ids, "s": surface, "ot": original_type},
    )
    occurrences: list[dict[str, Any]] = preview_data["entitySurfaceOccurrences"]
    jobs: list[dict[str, Any]] = []
    if not dry_run:
        mutation = (
            "mutation($ids:[ID!]!,$s:String!,$ot:String!,$a:String!,"
            "$et:String,$r:String){correctEntitySurface(documentIds:$ids,"
            "surface:$s,originalType:$ot,action:$a,entityType:$et,rationale:$r)"
            f"{{{JOB_FIELDS}}}}}"
        )
        data = await gql(
            mutation,
            {
                "ids": document_ids,
                "s": surface,
                "ot": original_type,
                "a": action,
                "et": entity_type,
                "r": rationale,
            },
        )
        jobs = data["correctEntitySurface"]
    return {
        "dryRun": dry_run,
        "applied": not dry_run,
        "scope": scope,
        "rule": {
            "surface": surface,
            "originalType": original_type,
            "action": action,
            "entityType": entity_type,
        },
        "occurrences": occurrences,
        "jobs": jobs,
    }


TOOLS: tuple[Any, ...] = (correct_entity_surface,)
