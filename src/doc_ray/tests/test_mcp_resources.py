"""MCP resource layer: `docray://` URIs and RFC 6570 templates (no network).

Block 3b — the addressing/ergonomics layer over the read tools. Resources are
declarative, host-cacheable handles for the same RLS-scoped data the tools serve:
they add addressing, not capability (a `resources/read` still lands its bytes in
the caller's context, exactly like a tool result).
"""

from __future__ import annotations

import json

import anyio
import pytest

from doc_ray.entrypoints.mcp import corpus, resources, server

_DOC = "11111111-1111-1111-1111-111111111111"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []
        self.headers = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        self.calls.append(json)
        self.headers.append(headers)
        payload = self._pages.pop(0) if self._pages else {"data": {"document": {"sentences": []}}}
        return _FakeResp(payload)


def _sentences(*rows):
    return {"data": {"document": {"sentences": list(rows)}}}


def _row(ordinal, text, cursor):
    return {
        "id": f"s{ordinal}",
        "ordinal": ordinal,
        "text": text,
        "charStart": ordinal * 10,
        "charEnd": ordinal * 10 + len(text),
        "cursor": cursor,
    }


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


def _patch(monkeypatch, pages):
    client = _FakeClient(pages)
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: client)
    return client


# --- the declared surface ---------------------------------------------------


def test_resources_declare_the_expected_uris_and_templates():
    uris = {spec.uri for spec in resources.RESOURCES}
    assert uris == {
        # concrete (listable) entry points
        "docray://corpus/documents",
        "docray://corpus/collections",
        "docray://corpus/manifest",
        # parameterized templates (drill-down)
        "docray://doc/{document_id}",
        "docray://doc/{document_id}/sentences/{start}-{end}",
        "docray://doc/{document_id}/pages/{page}",
        "docray://collection/{collection_id}",
        "docray://corpus/shards/{shard_count}",
    }


# --- regression: a template-only surface is INVISIBLE to clients -------------
# `resources/list` and `resources/templates/list` read disjoint registries in the
# SDK. A surface made only of parameterized templates yields resources/list == []
# — which is exactly what a client renders as "this server has no resources".


def test_resources_list_exposes_at_least_one_concrete_resource():
    mcp = server.build_server()
    listed = anyio.run(mcp.list_resources)
    assert listed, "resources/list is empty — clients that enumerate it see nothing"


def test_concrete_entry_point_uris_are_listed_by_resources_list():
    mcp = server.build_server()
    listed = {str(r.uri) for r in anyio.run(mcp.list_resources)}
    assert listed == {
        "docray://corpus/documents",
        "docray://corpus/collections",
        "docray://corpus/manifest",
    }


def test_parameterless_specs_land_in_resources_list_and_braced_ones_in_templates():
    """Structural guard against the disjoint-registry trap that caused this bug."""
    mcp = server.build_server()
    listed = {str(r.uri) for r in anyio.run(mcp.list_resources)}
    templated = {t.uriTemplate for t in anyio.run(mcp.list_resource_templates)}
    for spec in resources.RESOURCES:
        if "{" in spec.uri:
            assert spec.uri in templated, f"{spec.uri} missing from resources/templates/list"
        else:
            assert spec.uri in listed, f"{spec.uri} missing from resources/list"
    assert not (listed & templated)  # the two registries never overlap


def test_server_registers_the_resource_templates():
    mcp = server.build_server()
    templates = {t.uriTemplate for t in anyio.run(mcp.list_resource_templates)}
    assert "docray://doc/{document_id}/sentences/{start}-{end}" in templates
    assert "docray://doc/{document_id}" in templates
    assert "docray://corpus/shards/{shard_count}" in templates


def test_registered_templates_keep_their_descriptions():
    mcp = server.build_server()
    by_uri = {t.uriTemplate: t for t in anyio.run(mcp.list_resource_templates)}
    assert by_uri["docray://doc/{document_id}/sentences/{start}-{end}"].description


# --- reading through the template (params extracted + coerced) --------------


@pytest.mark.asyncio
async def test_reading_a_sentence_range_uri_forwards_the_ordinal_range(monkeypatch):
    client = _patch(monkeypatch, [_sentences(_row(10, "Ten.", "c10"), _row(11, "Eleven.", "c11"))])
    mcp = server.build_server()
    contents = list(await mcp.read_resource(f"docray://doc/{_DOC}/sentences/10-11"))
    assert "Ten." in contents[0].content and "Eleven." in contents[0].content
    # the range args reached the API (proving template params were parsed + coerced)
    variables = client.calls[0]["variables"]
    assert variables["fo"] == 10 and variables["to"] == 11
    assert variables["id"] == _DOC


@pytest.mark.asyncio
async def test_reading_a_page_uri_forwards_the_page_filter(monkeypatch):
    client = _patch(monkeypatch, [_sentences(_row(3, "On page five.", "c3"))])
    mcp = server.build_server()
    contents = list(await mcp.read_resource(f"docray://doc/{_DOC}/pages/5"))
    assert "On page five." in contents[0].content
    assert client.calls[0]["variables"]["pg"] == 5


@pytest.mark.asyncio
async def test_reading_a_document_uri_walks_to_exhaustion(monkeypatch):
    client = _patch(
        monkeypatch,
        [
            _sentences(_row(0, "First.", "c0"), _row(1, "Second.", "c1")),
            _sentences(_row(2, "Third.", "c2")),
            _sentences(),  # empty page ends the walk
        ],
    )
    mcp = server.build_server()
    contents = list(await mcp.read_resource(f"docray://doc/{_DOC}"))
    text = contents[0].content
    assert text.splitlines() == ["First.", "Second.", "Third."]  # in order, complete
    assert len(client.calls) == 3  # paged until empty


@pytest.mark.asyncio
async def test_full_document_read_marks_truncation_never_silently(monkeypatch):
    huge = "x" * (resources.MAX_RESOURCE_TEXT_CHARS + 500)
    _patch(monkeypatch, [_sentences(_row(0, huge, "c0")), _sentences()])
    mcp = server.build_server()
    (content,) = list(await mcp.read_resource(f"docray://doc/{_DOC}"))
    assert len(content.content) < len(huge) + 500  # bounded
    assert "truncated" in content.content  # explicit marker, never silent
    assert "/sentences/" in content.content  # points at the ranged template


@pytest.mark.asyncio
async def test_collection_uri_returns_json(monkeypatch):
    coll = {"id": "c1", "name": "Dune Series", "documentCount": 2, "documents": []}
    _patch(monkeypatch, [{"data": {"collection": coll}}])
    mcp = server.build_server()
    (content,) = list(await mcp.read_resource(f"docray://collection/{_DOC}"))
    assert json.loads(content.content)["name"] == "Dune Series"


@pytest.mark.asyncio
async def test_corpus_shards_uri_returns_the_manifest_json(monkeypatch):
    manifest = {"shardCount": 4, "totalDocuments": 9, "shards": []}
    client = _patch(monkeypatch, [{"data": {"corpusManifest": manifest}}])
    mcp = server.build_server()
    (content,) = list(await mcp.read_resource("docray://corpus/shards/4"))
    assert json.loads(content.content)["totalDocuments"] == 9
    assert client.calls[0]["variables"]["sc"] == 4


@pytest.mark.asyncio
async def test_reading_an_unknown_document_yields_empty_text(monkeypatch):
    _patch(monkeypatch, [{"data": {"document": None}}])
    mcp = server.build_server()
    (content,) = list(await mcp.read_resource(f"docray://doc/{_DOC}"))
    assert content.content == ""


@pytest.mark.asyncio
async def test_corpus_documents_entry_point_returns_the_document_index(monkeypatch):
    docs = [{"id": "d1", "title": "Dune Messiah", "docType": "epub"}]
    _patch(monkeypatch, [{"data": {"documents": docs}}])
    mcp = server.build_server()
    (content,) = list(await mcp.read_resource("docray://corpus/documents"))
    assert json.loads(content.content)[0]["title"] == "Dune Messiah"


@pytest.mark.asyncio
async def test_corpus_collections_entry_point_returns_the_collection_index(monkeypatch):
    colls = [{"id": "c1", "name": "Dune Series", "documentCount": 3}]
    _patch(monkeypatch, [{"data": {"collections": colls}}])
    mcp = server.build_server()
    (content,) = list(await mcp.read_resource("docray://corpus/collections"))
    assert json.loads(content.content)[0]["name"] == "Dune Series"


@pytest.mark.asyncio
async def test_corpus_manifest_entry_point_uses_the_default_shard_count(monkeypatch):
    manifest = {"shardCount": resources.DEFAULT_SHARD_COUNT, "totalDocuments": 3, "shards": []}
    client = _patch(monkeypatch, [{"data": {"corpusManifest": manifest}}])
    mcp = server.build_server()
    (content,) = list(await mcp.read_resource("docray://corpus/manifest"))
    assert json.loads(content.content)["shardCount"] == resources.DEFAULT_SHARD_COUNT
    assert client.calls[0]["variables"]["sc"] == resources.DEFAULT_SHARD_COUNT


@pytest.mark.asyncio
async def test_a_malformed_document_id_in_the_uri_errors_rather_than_returning_empty(monkeypatch):
    client = _patch(monkeypatch, [_sentences()])
    mcp = server.build_server()
    with pytest.raises(Exception) as err:  # noqa: PT011 - SDK wraps the tool error
        await mcp.read_resource("docray://doc/not-a-uuid/pages/1")
    assert "bad_input" in str(err.value) or "UUID" in str(err.value)
    assert client.calls == []  # rejected before any GraphQL round-trip


@pytest.mark.asyncio
async def test_an_unknown_docray_uri_is_rejected(monkeypatch):
    _patch(monkeypatch, [_sentences()])
    mcp = server.build_server()
    with pytest.raises(Exception):  # noqa: B017,PT011 - unknown resource
        await mcp.read_resource("docray://nope/whatever")


@pytest.mark.asyncio
async def test_resource_reads_forward_the_callers_token(monkeypatch):
    client = _patch(monkeypatch, [_sentences(_row(0, "Hi.", "c0")), _sentences()])
    corpus.caller_token.set("tok-resource")
    mcp = server.build_server()
    await mcp.read_resource(f"docray://doc/{_DOC}")
    # RLS travels with the resource read exactly as it does with a tool call
    assert client.headers[0]["Authorization"] == "Bearer tok-resource"
