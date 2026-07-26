"""MCP write tools forward each GraphQL mutation and map the result (no network).

Coverage mirrors the read-tool suite (``test_mcp_corpus.py``): every tool forwards
the caller's token, sends the exact mutation the API exposes, maps variables, and
returns the ``Job`` payload (or the scalar the mutation returns). Authorization is
NOT re-implemented here — the API's ``_require_write_audited`` /
``_require_document_write`` guards remain authoritative; a caller without the
``write`` scope gets the API's ``permission_denied`` mapped to a typed
``ToolCallError`` exactly as reads do.
"""

from __future__ import annotations

from typing import Any

import pytest

from doc_ray.entrypoints.mcp import corpus, mutations


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeResp:
        self.calls.append((url, json, headers))
        return _FakeResp(self.payload)


@pytest.fixture(autouse=True)
def _reset_token() -> None:
    corpus.caller_token.set("")


def _patch(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> _FakeClient:
    client = _FakeClient(payload)
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: client)
    return client


_DOC = "11111111-1111-1111-1111-111111111111"
_JOB = {"id": "job-1", "kind": "reannotate", "status": "queued", "documentId": _DOC}


@pytest.mark.asyncio
async def test_reannotate_forwards_mutation_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"reannotate": _JOB}})
    corpus.caller_token.set("tok-write")
    out = await mutations.reannotate(_DOC, lang="pt")
    assert out == _JOB
    _url, body, headers = client.calls[0]
    assert headers["Authorization"] == "Bearer tok-write"  # write travels with the token
    assert "reannotate(documentId:$id,lang:$lang)" in body["query"]
    assert body["query"].lstrip().startswith("mutation")
    assert body["variables"] == {"id": _DOC, "lang": "pt"}


@pytest.mark.asyncio
async def test_classify_document_forwards_mutation_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = {"id": "job-2", "kind": "classify_genre", "status": "queued", "documentId": _DOC}
    client = _patch(monkeypatch, {"data": {"classifyDocument": job}})
    corpus.caller_token.set("tok-write")
    out = await mutations.classify_document(_DOC)
    assert out == job
    _url, body, headers = client.calls[0]
    assert headers["Authorization"] == "Bearer tok-write"
    assert "classifyDocument(documentId:$id)" in body["query"]
    assert body["variables"] == {"id": _DOC}


@pytest.mark.asyncio
async def test_confirm_document_classification_forwards_mutation_and_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {"documentId": _DOC, "genre": "fiction", "status": "confirmed", "method": "human"}
    client = _patch(monkeypatch, {"data": {"confirmDocumentClassification": row}})
    corpus.caller_token.set("tok-write")
    out = await mutations.confirm_document_classification(_DOC, "fiction", subtype="novel")
    assert out == row
    _url, body, headers = client.calls[0]
    assert headers["Authorization"] == "Bearer tok-write"
    assert "confirmDocumentClassification(documentId:$id,genre:$g" in body["query"]
    assert body["variables"] == {"id": _DOC, "g": "fiction", "st": "novel", "r": None}


@pytest.mark.asyncio
async def test_classify_document_rejects_bad_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"data": {}})
    with pytest.raises(mutations.ToolCallError):
        await mutations.classify_document("not-a-uuid")


@pytest.mark.asyncio
async def test_reextract_document_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"reextractDocument": _JOB}})
    assert await mutations.reextract_document(_DOC) == _JOB
    assert "reextractDocument(documentId:$id)" in client.calls[0][1]["query"]


@pytest.mark.asyncio
async def test_annotate_document_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"annotateDocument": _JOB}})
    await mutations.annotate_document(_DOC, lang="en")
    body = client.calls[0][1]
    assert "annotateDocument(documentId:$id,lang:$lang)" in body["query"]
    assert body["variables"] == {"id": _DOC, "lang": "en"}


@pytest.mark.asyncio
async def test_semantic_enrich_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"semanticEnrichDocument": _JOB}})
    await mutations.semantic_enrich_document(_DOC)
    assert "semanticEnrichDocument(documentId:$id)" in client.calls[0][1]["query"]


@pytest.mark.asyncio
async def test_canonicalize_forwards_resolve_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"canonicalizeDocument": _JOB}})
    await mutations.canonicalize_document(_DOC, resolve=True)
    body = client.calls[0][1]
    assert "canonicalizeDocument(documentId:$id,resolve:$r)" in body["query"]
    assert body["variables"] == {"id": _DOC, "r": True}


@pytest.mark.asyncio
async def test_canonicalize_defaults_resolve_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM cost is opt-in: the tool must default resolve to false (proposal §5.1 / D-018)."""
    client = _patch(monkeypatch, {"data": {"canonicalizeDocument": _JOB}})
    await mutations.canonicalize_document(_DOC)
    assert client.calls[0][1]["variables"]["r"] is False


@pytest.mark.asyncio
async def test_suggest_seeds_forwards_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"suggestSeeds": _JOB}})
    await mutations.suggest_seeds(_DOC, limit=10)
    body = client.calls[0][1]
    assert "suggestSeeds(documentId:$id,limit:$l)" in body["query"]
    assert body["variables"] == {"id": _DOC, "l": 10}


@pytest.mark.asyncio
async def test_suggest_document_metadata_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"suggestDocumentMetadata": _JOB}})
    await mutations.suggest_document_metadata(_DOC)
    assert "suggestDocumentMetadata(documentId:$id)" in client.calls[0][1]["query"]


@pytest.mark.asyncio
async def test_update_metadata_sends_only_the_document_id_and_passed_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch(monkeypatch, {"data": {"updateDocumentMetadata": _JOB}})
    await mutations.update_document_metadata(_DOC, title="New", tags=["a", "b"])
    body = client.calls[0][1]
    assert (
        "updateDocumentMetadata(documentId:$id,title:$t,description:$d,tags:$tags,authors:$authors)"
        in body["query"]
    )
    # description omitted -> null forwarded (server treats null as "unchanged")
    assert body["variables"] == {
        "id": _DOC,
        "t": "New",
        "d": None,
        "tags": ["a", "b"],
        "authors": None,
    }


@pytest.mark.asyncio
async def test_update_metadata_forwards_authors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"updateDocumentMetadata": _JOB}})
    await mutations.update_document_metadata(_DOC, authors=["Brian Herbert", "Kevin J. Anderson"])
    body = client.calls[0][1]
    assert "authors:$authors" in body["query"]
    assert body["variables"]["authors"] == ["Brian Herbert", "Kevin J. Anderson"]
    # unset fields still forwarded as null (server treats null as "unchanged")
    assert body["variables"]["t"] is None and body["variables"]["tags"] is None


@pytest.mark.asyncio
async def test_bulk_tag_documents_returns_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_JOB, {**_JOB, "id": "job-2"}]
    client = _patch(monkeypatch, {"data": {"bulkTagDocuments": jobs}})
    out = await mutations.bulk_tag_documents([_DOC], tags=["x"])
    assert out == jobs
    body = client.calls[0][1]
    assert "bulkTagDocuments(documentIds:$ids,tags:$tags)" in body["query"]
    assert body["variables"] == {"ids": [_DOC], "tags": ["x"]}


@pytest.mark.asyncio
async def test_seed_approved_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"seedApproved": _JOB}})
    await mutations.seed_approved(_DOC)
    assert "seedApproved(documentId:$id)" in client.calls[0][1]["query"]


@pytest.mark.asyncio
async def test_decide_seeds_returns_rows_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"decideSeeds": 3}})
    out = await mutations.decide_seeds(approve_ids=["s1", "s2"], reject_ids=["s3"])
    assert out == 3
    body = client.calls[0][1]
    assert "decideSeeds(approveIds:$a,rejectIds:$r)" in body["query"]
    assert body["variables"] == {"a": ["s1", "s2"], "r": ["s3"]}


@pytest.mark.asyncio
async def test_decide_document_metadata_suggestions_returns_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch(monkeypatch, {"data": {"decideDocumentMetadataSuggestions": 1}})
    out = await mutations.decide_document_metadata_suggestions(approve_ids=["m1"])
    assert out == 1
    assert (
        "decideDocumentMetadataSuggestions(approveIds:$a,rejectIds:$r)"
        in client.calls[0][1]["query"]
    )


@pytest.mark.asyncio
async def test_malformed_document_id_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch(monkeypatch, {"data": {"reannotate": _JOB}})
    with pytest.raises(corpus.ToolCallError) as excinfo:
        await mutations.reannotate("not-a-uuid")
    assert excinfo.value.kind == "bad_input"
    assert client.calls == []  # never left the process


@pytest.mark.asyncio
async def test_bulk_tag_requires_at_least_one_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"bulkTagDocuments": []}})
    with pytest.raises(corpus.ToolCallError) as excinfo:
        await mutations.bulk_tag_documents([_DOC], tags=["  ", ""])
    assert excinfo.value.kind == "bad_input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_write_without_scope_maps_to_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The API is authoritative: a 200-wrapped permission-denied GraphQL error is typed."""
    client = _patch(  # noqa: F841 — asserted via the raise, not the calls
        monkeypatch,
        {
            "errors": [
                {"message": "API mutation requires an authenticated principal with write scope"}
            ]
        },
    )
    with pytest.raises(corpus.ToolCallError) as excinfo:
        await mutations.reannotate(_DOC)
    assert excinfo.value.kind == "permission_denied"


_COLL = "22222222-2222-2222-2222-222222222222"
_COLLECTION = {"id": _COLL, "name": "Dune Series", "kind": "manual", "documentCount": 0}


@pytest.mark.asyncio
async def test_create_collection_forwards_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"createCollection": _COLLECTION}})
    out = await mutations.create_collection(
        "Dune Series", description="Herbert fiction", tags=["dune"]
    )
    assert out == _COLLECTION
    body = client.calls[0][1]
    assert (
        "createCollection(name:$n,description:$d,kind:$k,rule:$r,docType:$dt,status:$s,tags:$t)"
        in body["query"]
    )
    assert body["variables"] == {
        "n": "Dune Series",
        "d": "Herbert fiction",
        "k": "manual",
        "r": None,
        "dt": None,
        "s": None,
        "t": ["dune"],
    }


@pytest.mark.asyncio
async def test_create_collection_requires_a_name(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"createCollection": _COLLECTION}})
    with pytest.raises(corpus.ToolCallError) as ei:
        await mutations.create_collection("  ")
    assert ei.value.kind == "bad_input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_add_document_to_collection_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"addDocumentToCollection": _COLLECTION}})
    await mutations.add_document_to_collection(_COLL, _DOC)
    body = client.calls[0][1]
    assert "addDocumentToCollection(collectionId:$c,documentId:$d)" in body["query"]
    assert body["variables"] == {"c": _COLL, "d": _DOC}


@pytest.mark.asyncio
async def test_remove_document_from_collection_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"removeDocumentFromCollection": _COLLECTION}})
    await mutations.remove_document_from_collection(_COLL, _DOC)
    assert (
        "removeDocumentFromCollection(collectionId:$c,documentId:$d)" in client.calls[0][1]["query"]
    )


@pytest.mark.asyncio
async def test_collection_membership_rejects_bad_ids_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch(monkeypatch, {"data": {"addDocumentToCollection": _COLLECTION}})
    with pytest.raises(corpus.ToolCallError):
        await mutations.add_document_to_collection("not-a-uuid", _DOC)
    with pytest.raises(corpus.ToolCallError):
        await mutations.remove_document_from_collection(_COLL, "not-a-uuid")
    assert client.calls == []


@pytest.mark.asyncio
async def test_update_collection_forwards_name_and_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch(monkeypatch, {"data": {"updateCollection": _COLLECTION}})
    out = await mutations.update_collection(_COLL, name="AI & Business")
    assert out == _COLLECTION
    body = client.calls[0][1]
    assert "updateCollection(collectionId:$c,name:$n,description:$d)" in body["query"]
    assert body["variables"] == {"c": _COLL, "n": "AI & Business", "d": None}


@pytest.mark.asyncio
async def test_update_collection_rejects_bad_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"updateCollection": _COLLECTION}})
    with pytest.raises(corpus.ToolCallError):
        await mutations.update_collection("not-a-uuid", name="x")
    assert client.calls == []


@pytest.mark.asyncio
async def test_delete_collection_forwards_and_returns_bool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _patch(monkeypatch, {"data": {"deleteCollection": True}})
    out = await mutations.delete_collection(_COLL)
    assert out is True
    body = client.calls[0][1]
    assert "deleteCollection(collectionId:$c)" in body["query"]
    assert body["variables"] == {"c": _COLL}


@pytest.mark.asyncio
async def test_delete_collection_rejects_bad_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"deleteCollection": True}})
    with pytest.raises(corpus.ToolCallError):
        await mutations.delete_collection("not-a-uuid")
    assert client.calls == []


@pytest.mark.asyncio
async def test_promote_collection_to_lens_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"promoteCollectionToLens": _COLLECTION}})
    out = await mutations.promote_collection_to_lens(_COLL)
    assert out == _COLLECTION
    body = client.calls[0][1]
    assert "promoteCollectionToLens(collectionId:$c,parentLensId:$p)" in body["query"]
    assert body["variables"] == {"c": _COLL, "p": None}


@pytest.mark.asyncio
async def test_promote_collection_to_lens_forwards_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"promoteCollectionToLens": _COLLECTION}})
    await mutations.promote_collection_to_lens(_COLL, parent_lens_id=_DOC)
    assert client.calls[0][1]["variables"] == {"c": _COLL, "p": _DOC}


@pytest.mark.asyncio
async def test_promote_collection_rejects_bad_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"promoteCollectionToLens": _COLLECTION}})
    with pytest.raises(corpus.ToolCallError):
        await mutations.promote_collection_to_lens(_COLL, parent_lens_id="not-a-uuid")
    assert client.calls == []


@pytest.mark.asyncio
async def test_demote_lens_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"demoteLens": _COLLECTION}})
    out = await mutations.demote_lens(_COLL)
    assert out == _COLLECTION
    assert "demoteLens(collectionId:$c)" in client.calls[0][1]["query"]
    assert client.calls[0][1]["variables"] == {"c": _COLL}


@pytest.mark.asyncio
async def test_set_document_default_lens_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = {"id": _DOC, "title": "Dune", "defaultLensId": _COLL}
    client = _patch(monkeypatch, {"data": {"setDocumentDefaultLens": doc}})
    out = await mutations.set_document_default_lens(_DOC, lens_id=_COLL)
    assert out == doc
    body = client.calls[0][1]
    assert "setDocumentDefaultLens(documentId:$d,lensId:$l)" in body["query"]
    assert body["variables"] == {"d": _DOC, "l": _COLL}


@pytest.mark.asyncio
async def test_set_document_default_lens_clears_with_null(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = {"id": _DOC, "title": "Dune", "defaultLensId": None}
    client = _patch(monkeypatch, {"data": {"setDocumentDefaultLens": doc}})
    await mutations.set_document_default_lens(_DOC)
    assert client.calls[0][1]["variables"] == {"d": _DOC, "l": None}


def test_tools_tuple_is_the_reprocessing_and_curation_writers() -> None:
    names = {tool.__name__ for tool in mutations.TOOLS}
    assert names == {
        "reannotate",
        "reextract_document",
        "annotate_document",
        "semantic_enrich_document",
        "embed_document_sentences",
        "canonicalize_document",
        "suggest_seeds",
        "suggest_document_metadata",
        "update_document_metadata",
        "bulk_tag_documents",
        "correct_entity_mention",
        "classify_document",
        "confirm_document_classification",
        "decide_seeds",
        "decide_document_metadata_suggestions",
        "decide_visual_captions",
        "seed_approved",
        "create_collection",
        "add_document_to_collection",
        "remove_document_from_collection",
        "update_collection",
        "delete_collection",
        "promote_collection_to_lens",
        "demote_lens",
        "set_document_default_lens",
    }
    # Destructive DOCUMENT verbs stay off MCP (delete_collection removes only the
    # container, never the documents, so it is exposed).
    assert not {"delete_document", "archive_document", "create_backup", "cancel_job"} & names


@pytest.mark.asyncio
async def test_decide_visual_captions_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _patch(monkeypatch, {"data": {"decideVisualCaptions": 2}})

    updated = await mutations.decide_visual_captions(
        approve_ids=["01APPROVE"], reject_ids=["01REJECT"]
    )

    assert updated == 2
    body = client.calls[0][1]
    assert "decideVisualCaptions" in body["query"]
    assert body["variables"] == {"a": ["01APPROVE"], "r": ["01REJECT"]}
