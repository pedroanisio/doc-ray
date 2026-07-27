"""MCP whole-document traversal + corpus-sharding read tools (no network).

Split out of test_mcp_corpus.py (D-015): the sequential-read walk
(`read_document_sentences`) and the corpus partition tools (`corpus_manifest` /
`corpus_shard`) — the map-reduce reading surface — with the same fake-transport
pattern as the rest of the corpus read-tool suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

# Import the server eagerly so the `mcp` SDK loads (with the real httpx) before
# any test patches httpx.AsyncClient — tokens._current_request() lazily imports it.
from doc_ray.entrypoints.mcp import corpus, server  # noqa: F401

_DOC = "11111111-1111-1111-1111-111111111111"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        self.calls.append((url, json, headers))
        return _FakeResp(self.payload)


@pytest.fixture(autouse=True)
def _reset_token():
    corpus.caller_token.set("")
    yield


def _patch(monkeypatch, payload):
    client = _FakeClient(payload)
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=client))
    return client


# --- read_document_sentences: exhaustive, in-order, citation-anchored walk ----


@pytest.mark.asyncio
async def test_read_document_sentences_forwards_and_maps(monkeypatch):
    rows = [
        {
            "id": "s0",
            "ordinal": 0,
            "text": "Chapter one.",
            "charStart": 0,
            "charEnd": 12,
            "cursor": "c0",
        },
        {
            "id": "s1",
            "ordinal": 1,
            "text": "It begins.",
            "charStart": 13,
            "charEnd": 23,
            "cursor": "c1",
        },
    ]
    client = _patch(monkeypatch, {"data": {"document": {"sentences": rows}}})
    corpus.caller_token.set("tok-read")
    out = await corpus.read_document_sentences(_DOC, limit=2)
    assert out == rows
    url, body, headers = client.calls[0]
    assert headers["Authorization"] == "Bearer tok-read"  # RLS travels with the token
    assert "document(id:$id)" in body["query"]
    assert "sentences(limit:$l,after:$a,fromOrdinal:$fo,toOrdinal:$to,page:$pg)" in body["query"]
    # every citation handle is selected so slices stay attributable
    for field in ("ordinal", "text", "charStart", "charEnd", "cursor"):
        assert field in body["query"]
    # range/page args default to null (a plain sequential walk) when unset
    assert body["variables"] == {"id": _DOC, "l": 2, "a": None, "fo": None, "to": None, "pg": None}


@pytest.mark.asyncio
async def test_read_document_sentences_forwards_ordinal_range_and_page(monkeypatch):
    client = _patch(monkeypatch, {"data": {"document": {"sentences": []}}})
    await corpus.read_document_sentences(_DOC, from_ordinal=4000, to_ordinal=4200, page=50)
    _, body, _ = client.calls[0]
    assert body["variables"]["fo"] == 4000
    assert body["variables"]["to"] == 4200
    assert body["variables"]["pg"] == 50


@pytest.mark.asyncio
async def test_read_document_sentences_rejects_inverted_ordinal_range(monkeypatch):
    client = _patch(monkeypatch, {"data": {"document": {"sentences": []}}})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.read_document_sentences(_DOC, from_ordinal=4200, to_ordinal=4000)
    assert err.value.kind == "bad_input"
    assert client.calls == []  # rejected before any GraphQL round-trip


@pytest.mark.asyncio
async def test_read_document_sentences_forwards_after_cursor(monkeypatch):
    client = _patch(monkeypatch, {"data": {"document": {"sentences": []}}})
    await corpus.read_document_sentences(_DOC, limit=5, after="cur-7")
    _, body, _ = client.calls[0]
    assert body["variables"]["a"] == "cur-7"
    assert "after:$a" in body["query"]


@pytest.mark.asyncio
async def test_read_document_sentences_returns_empty_for_unknown_document(monkeypatch):
    _patch(monkeypatch, {"data": {"document": None}})
    assert await corpus.read_document_sentences(_DOC) == []


@pytest.mark.asyncio
async def test_read_document_sentences_rejects_bad_uuid_before_network(monkeypatch):
    client = _patch(monkeypatch, {"data": {"document": {"sentences": []}}})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.read_document_sentences("not-a-uuid")
    assert err.value.kind == "bad_input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_read_document_sentences_returns_text_verbatim_not_clipped(monkeypatch):
    # Unlike KWIC windows, the sequential-read text is NOT clipped: the whole
    # point is exhaustive delivery, and the caller bounds payload via `limit`.
    mega = "z" * 20_000
    _patch(
        monkeypatch,
        {"data": {"document": {"sentences": [{"ordinal": 0, "text": mega, "cursor": "c0"}]}}},
    )
    (row,) = await corpus.read_document_sentences(_DOC)
    assert row["text"] == mega  # verbatim, no "… [+N chars]" marker


@pytest.mark.asyncio
async def test_walking_read_document_sentences_reconstructs_the_full_ordinal_sequence(monkeypatch):
    """Driving the tool with successive `after` cursors covers every sentence, in order."""
    # A fake corpus of 7 ordered sentences the fake API pages 3 at a time by cursor.
    full = [
        {
            "ordinal": n,
            "text": f"s{n}",
            "charStart": n * 10,
            "charEnd": n * 10 + 2,
            "cursor": f"c{n}",
        }
        for n in range(7)
    ]

    class _PagingClient:
        def __init__(self):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers):
            after = json["variables"]["a"]
            start = (
                0
                if after is None
                else next(i for i, r in enumerate(full) if r["cursor"] == after) + 1
            )
            page = full[start : start + 3]
            self.calls.append(after)
            return _FakeResp({"data": {"document": {"sentences": page}}})

    client = _PagingClient()
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=client))

    walked, after = [], None
    while True:
        page = await corpus.read_document_sentences(_DOC, limit=3, after=after)
        if not page:
            break
        walked.extend(page)
        after = page[-1]["cursor"]

    assert [r["ordinal"] for r in walked] == list(range(7))  # complete, in order, no gaps/dupes
    # threaded the cursor forward each page, incl. the final empty-page probe that ends the walk
    assert client.calls == [None, "c2", "c5", "c6"]


# --- corpus sharding: manifest + per-shard document pages -------------------


@pytest.mark.asyncio
async def test_corpus_manifest_forwards_and_maps(monkeypatch):
    manifest = {
        "shardCount": 4,
        "totalDocuments": 10,
        "shards": [{"index": i, "documentCount": c} for i, c in enumerate([3, 2, 3, 2])],
    }
    client = _patch(monkeypatch, {"data": {"corpusManifest": manifest}})
    out = await corpus.corpus_manifest(4)
    assert out == manifest
    _url, body, _headers = client.calls[0]
    assert "corpusManifest(shardCount:$sc)" in body["query"]
    assert "shards{index documentCount}" in body["query"]
    assert body["variables"] == {"sc": 4}


@pytest.mark.asyncio
async def test_corpus_shard_forwards_and_maps(monkeypatch):
    rows = [{"id": "d1", "title": "Doc", "docType": "pdf", "cursor": "k1"}]
    client = _patch(monkeypatch, {"data": {"corpusShard": rows}})
    corpus.caller_token.set("tok-read")
    out = await corpus.corpus_shard(2, 4, limit=25)
    assert out == rows
    _url, body, headers = client.calls[0]
    assert headers["Authorization"] == "Bearer tok-read"  # shard is RLS-scoped to the token
    assert "corpusShard(shardIndex:$si,shardCount:$sc,limit:$l,after:$a)" in body["query"]
    assert body["variables"] == {"si": 2, "sc": 4, "l": 25, "a": None}


@pytest.mark.asyncio
async def test_corpus_shard_forwards_after_cursor(monkeypatch):
    client = _patch(monkeypatch, {"data": {"corpusShard": []}})
    await corpus.corpus_shard(0, 8, limit=5, after="cur-shard")
    _, body, _ = client.calls[0]
    assert body["variables"]["a"] == "cur-shard"
    assert "after:$a" in body["query"]


@pytest.mark.parametrize(
    "index,count",
    [(0, 0), (0, -1), (-1, 4), (4, 4), (5, 4)],  # count<1, index out of [0,count)
)
@pytest.mark.asyncio
async def test_corpus_shard_rejects_invalid_shard_arguments(monkeypatch, index, count):
    client = _patch(monkeypatch, {"data": {"corpusShard": []}})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.corpus_shard(index, count)
    assert err.value.kind == "bad_input"
    assert client.calls == []  # rejected before any GraphQL round-trip


@pytest.mark.parametrize("count", [0, -3])
@pytest.mark.asyncio
async def test_corpus_manifest_rejects_invalid_shard_count(monkeypatch, count):
    client = _patch(monkeypatch, {"data": {"corpusManifest": {}}})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.corpus_manifest(count)
    assert err.value.kind == "bad_input"
    assert client.calls == []
