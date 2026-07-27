"""MCP contracts for dry-run-first exact-surface NER corrections."""

from __future__ import annotations

import inspect

import pytest

from doc_ray.entrypoints.mcp import mutations_surface
from doc_ray.entrypoints.mcp.corpus import ToolCallError

_DOC = "11111111-1111-1111-1111-111111111111"
_DOC_2 = "22222222-2222-2222-2222-222222222222"
_COLLECTION = "33333333-3333-3333-3333-333333333333"
_PREVIEW = {
    "documentId": _DOC,
    "surface": "Apple",
    "originalType": "ORG",
    "occurrenceCount": 2,
    "countCap": 1000,
    "countCapped": False,
    "samples": [],
}


@pytest.mark.asyncio
async def test_default_is_preview_only_and_never_sends_the_mutation(monkeypatch) -> None:
    calls = []

    async def gql(document, variables):
        calls.append((document, variables))
        return {"entitySurfaceOccurrences": [_PREVIEW]}

    monkeypatch.setattr(mutations_surface, "gql", gql)

    result = await mutations_surface.correct_entity_surface(
        "Apple", "ORG", "reject_surface", document_id=_DOC
    )

    assert len(calls) == 1
    assert calls[0][0].lstrip().startswith("query")
    assert "correctEntitySurface" not in calls[0][0]
    assert calls[0][1] == {"ids": [_DOC], "s": "Apple", "ot": "ORG"}
    assert result == {
        "dryRun": True,
        "applied": False,
        "scope": {"kind": "document", "id": _DOC, "documentCount": 1},
        "rule": {
            "surface": "Apple",
            "originalType": "ORG",
            "action": "reject_surface",
            "entityType": None,
        },
        "occurrences": [_PREVIEW],
        "jobs": [],
    }


@pytest.mark.asyncio
async def test_explicit_commit_previews_then_writes_the_same_document_ids(monkeypatch) -> None:
    calls = []
    jobs = [{"id": "job-1", "kind": "apply_corrections", "documentId": _DOC}]

    async def gql(document, variables):
        calls.append((document, variables))
        if document.lstrip().startswith("query"):
            return {"entitySurfaceOccurrences": [_PREVIEW]}
        return {"correctEntitySurface": jobs}

    monkeypatch.setattr(mutations_surface, "gql", gql)
    result = await mutations_surface.correct_entity_surface(
        "Apple",
        "ORG",
        "reclassify_surface",
        document_id=_DOC,
        entity_type="PRODUCT",
        rationale="brand",
        dry_run=False,
    )

    assert len(calls) == 2
    assert calls[1][0].lstrip().startswith("mutation")
    assert "correctEntitySurface(documentIds:$ids" in calls[1][0]
    assert calls[1][1] == {
        "ids": [_DOC],
        "s": "Apple",
        "ot": "ORG",
        "a": "reclassify_surface",
        "et": "PRODUCT",
        "r": "brand",
    }
    assert result["dryRun"] is False and result["applied"] is True
    assert result["occurrences"] == [_PREVIEW]
    assert result["jobs"] == jobs


@pytest.mark.asyncio
async def test_collection_scope_is_resolved_to_an_explicit_complete_id_list(monkeypatch) -> None:
    calls = []

    async def collection(collection_id, document_limit):
        assert (collection_id, document_limit) == (_COLLECTION, 200)
        return {
            "id": _COLLECTION,
            "documentCount": 2,
            "documents": [{"id": _DOC}, {"id": _DOC_2}],
        }

    async def gql(document, variables):
        calls.append((document, variables))
        return {"entitySurfaceOccurrences": [_PREVIEW]}

    monkeypatch.setattr(mutations_surface, "get_collection", collection)
    monkeypatch.setattr(mutations_surface, "gql", gql)

    result = await mutations_surface.correct_entity_surface(
        "Apple", "ORG", "reject_surface", collection_id=_COLLECTION
    )

    assert calls[0][1]["ids"] == [_DOC, _DOC_2]
    assert result["scope"] == {
        "kind": "collection",
        "id": _COLLECTION,
        "documentCount": 2,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "collection",
    [
        None,
        {"id": _COLLECTION, "documentCount": 0, "documents": []},
        {"id": _COLLECTION, "documentCount": 1, "documents": [{"id": None}]},
        {
            "id": _COLLECTION,
            "documentCount": 2,
            "documents": [{"id": _DOC}, {"id": _DOC}],
        },
        {
            "id": _COLLECTION,
            "documentCount": 201,
            "documents": [{"id": _DOC}],
        },
        {
            "id": _COLLECTION,
            "documentCount": 200,
            "documents": [{"id": str(index)} for index in range(200)],
        },
    ],
)
async def test_collection_scope_refuses_unknown_empty_or_unprovably_complete_results(
    monkeypatch, collection
) -> None:
    calls = []

    async def get(*args, **kwargs):
        return collection

    async def gql(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(mutations_surface, "get_collection", get)
    monkeypatch.setattr(mutations_surface, "gql", gql)

    with pytest.raises(ToolCallError) as excinfo:
        await mutations_surface.correct_entity_surface(
            "Apple", "ORG", "reject_surface", collection_id=_COLLECTION
        )
    assert excinfo.value.kind == "bad_input"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"document_id": _DOC, "collection_id": _COLLECTION},
        {"document_id": "not-a-uuid"},
        {"document_id": _DOC, "surface": " "},
        {"document_id": _DOC, "original_type": " "},
        {"document_id": _DOC, "action": "reject"},
        {"document_id": _DOC, "action": "reclassify_surface"},
        {
            "document_id": _DOC,
            "action": "reclassify_surface",
            "entity_type": "ORG",
        },
    ],
)
async def test_bad_inputs_fail_locally_before_any_graphql_call(monkeypatch, kwargs) -> None:
    calls = []

    async def gql(*args, **kw):
        calls.append((args, kw))
        return {}

    monkeypatch.setattr(mutations_surface, "gql", gql)
    values = {"surface": "Apple", "original_type": "ORG", "action": "reject_surface"}
    values.update(kwargs)
    with pytest.raises(ToolCallError) as excinfo:
        await mutations_surface.correct_entity_surface(**values)
    assert excinfo.value.kind == "bad_input"
    assert calls == []


def test_tool_signature_and_existing_adjust_span_docs_are_explicit() -> None:
    from doc_ray.entrypoints.mcp import mutations

    signature = inspect.signature(mutations_surface.correct_entity_surface)
    assert list(signature.parameters) == [
        "surface",
        "original_type",
        "action",
        "document_id",
        "collection_id",
        "entity_type",
        "rationale",
        "dry_run",
    ]
    assert signature.parameters["dry_run"].default is True
    doc = " ".join((mutations.correct_entity_mention.__doc__ or "").split()).lower()
    assert "adjust_span" in doc and "entity_type" in doc and "retype" in doc


def test_tool_annotations_match_the_mixed_preview_commit_contract() -> None:
    from doc_ray.entrypoints.mcp.tool_meta import meta_for

    hints = meta_for("correct_entity_surface").annotations
    assert hints.readOnlyHint is False
    assert hints.destructiveHint is False
    assert hints.idempotentHint is True
    assert hints.openWorldHint is False
