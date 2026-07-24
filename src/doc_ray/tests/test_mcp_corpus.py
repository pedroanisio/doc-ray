"""MCP corpus tools forward the caller's bearer token to GraphQL and map the result (no network)."""

from __future__ import annotations

import pytest

# Import the server eagerly so the `mcp` SDK loads (with the real httpx) before
# any test patches httpx.AsyncClient — tokens._current_request() lazily imports
# it, and the SDK evaluates `httpx.AsyncClient | None` at import time.
from doc_ray.entrypoints.mcp import corpus, server  # noqa: F401


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
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: client)
    return client


_DOC = "11111111-1111-1111-1111-111111111111"


@pytest.mark.asyncio
async def test_search_forwards_bearer_and_maps_variables(monkeypatch):
    client = _patch(monkeypatch, {"data": {"searchSentences": [{"id": "1", "hit": "Berlin"}]}})
    corpus.caller_token.set("tok-abc")
    out = await corpus.search_sentences("Berlin", limit=5)
    assert out == [{"id": "1", "hit": "Berlin"}]
    url, body, headers = client.calls[0]
    assert url == corpus.API_URL
    assert headers["Authorization"] == "Bearer tok-abc"  # RLS travels with the caller's token
    assert body["variables"] == {"t": "Berlin", "d": None, "l": 5, "a": None}


# --- cursor pagination ------------------------------------------------------


@pytest.mark.asyncio
async def test_search_sentences_forwards_after_cursor(monkeypatch):
    client = _patch(monkeypatch, {"data": {"searchSentences": []}})
    await corpus.search_sentences("Berlin", limit=5, after="cur-9")
    _, body, _ = client.calls[0]
    assert body["variables"]["a"] == "cur-9"
    assert "after:$a" in body["query"]


@pytest.mark.asyncio
async def test_list_documents_forwards_after_cursor(monkeypatch):
    client = _patch(monkeypatch, {"data": {"documents": []}})
    await corpus.list_documents(limit=3, after="cur-42")
    _, body, _ = client.calls[0]
    assert body["variables"]["a"] == "cur-42"
    assert "after:$a" in body["query"]


@pytest.mark.asyncio
async def test_list_documents_selects_the_fields_the_corpus_views_need(monkeypatch):
    # The webapp corpus view modes (BL-143) key on language and sentence count;
    # an MCP client building equivalent views needs those in the summary too, so
    # the surfaces stay consistent about what a document row exposes.
    client = _patch(monkeypatch, {"data": {"documents": []}})
    await corpus.list_documents(limit=5)
    _, body, _ = client.calls[0]
    for field in ("language", "sentenceCount"):
        assert field in body["query"], f"list_documents must select {field}"


# --- KWIC window clipping ---------------------------------------------------


@pytest.mark.asyncio
async def test_search_sentences_clips_oversized_kwic_windows(monkeypatch):
    # A TOC segmented as one mega-sentence must not flood the response; windows
    # are clipped with an explicit marker, never silently.
    mega = "x" * 15_000
    _patch(
        monkeypatch,
        {
            "data": {
                "searchSentences": [
                    {"id": "1", "pre": mega, "hit": "Berlin", "post": "small tail."}
                ]
            }
        },
    )
    (row,) = await corpus.search_sentences("Berlin")
    assert row["pre"] == "x" * corpus.MAX_KWIC_WINDOW_CHARS + "… [+14500 chars]"
    assert row["hit"] == "Berlin"  # small windows pass through untouched
    assert row["post"] == "small tail."


@pytest.mark.asyncio
async def test_lexeme_kwic_clips_oversized_windows(monkeypatch):
    mega = "y" * 1_000
    _patch(
        monkeypatch,
        {
            "data": {
                "lexeme": {
                    "uid": "u1",
                    "occurrences": [{"kwic": mega, "pre": mega, "hit": "run", "post": "ok"}],
                }
            }
        },
    )
    (row,) = await corpus.lexeme_kwic("u1")
    assert row["kwic"] == "y" * corpus.MAX_KWIC_WINDOW_CHARS + "… [+500 chars]"
    assert row["pre"] == "y" * corpus.MAX_KWIC_WINDOW_CHARS + "… [+500 chars]"
    assert row["hit"] == "run"
    assert row["post"] == "ok"


@pytest.mark.asyncio
async def test_anonymous_sends_no_auth_header(monkeypatch):
    client = _patch(monkeypatch, {"data": {"fullTextSearch": []}})
    corpus.caller_token.set("")
    await corpus.full_text_search("x")
    assert "Authorization" not in client.calls[0][2]


@pytest.mark.asyncio
async def test_graphql_error_raises_runtimeerror(monkeypatch):
    _patch(monkeypatch, {"errors": [{"message": "permission denied"}]})
    with pytest.raises(RuntimeError, match="permission denied"):
        await corpus.get_document("00000000-0000-0000-0000-000000000000")


@pytest.mark.asyncio
async def test_get_document_returns_none_when_null(monkeypatch):
    _patch(monkeypatch, {"data": {"document": None}})
    assert await corpus.get_document("00000000-0000-0000-0000-000000000000") is None


@pytest.mark.asyncio
async def test_get_job_forwards_and_maps(monkeypatch):
    """A caller must be able to poll a job it enqueued via a write tool."""
    job = {"id": "job-9", "kind": "reannotate", "status": "succeeded", "documentId": None}
    client = _patch(monkeypatch, {"data": {"job": job}})
    out = await corpus.get_job("job-9")
    assert out == job
    _url, body, _headers = client.calls[0]
    assert "job(id:$id)" in body["query"]
    assert body["variables"] == {"id": "job-9"}


@pytest.mark.asyncio
async def test_get_job_returns_none_when_unknown(monkeypatch):
    _patch(monkeypatch, {"data": {"job": None}})
    assert await corpus.get_job("nope") is None


@pytest.mark.asyncio
async def test_list_collections_forwards_and_maps(monkeypatch):
    rows = [{"id": "c1", "name": "Dune Series", "kind": "manual", "documentCount": 3}]
    client = _patch(monkeypatch, {"data": {"collections": rows}})
    out = await corpus.list_collections(limit=10, kind="manual")
    assert out == rows
    _url, body, _headers = client.calls[0]
    assert "collections(limit:$l,kind:$k,after:$a)" in body["query"]
    assert body["variables"] == {"l": 10, "k": "manual", "a": None}


@pytest.mark.asyncio
async def test_get_collection_forwards_with_documents(monkeypatch):
    coll = {"id": "c1", "name": "Dune Series", "documentCount": 3, "documents": []}
    client = _patch(monkeypatch, {"data": {"collection": coll}})
    out = await corpus.get_collection("00000000-0000-0000-0000-000000000000")
    assert out == coll
    body = client.calls[0][1]
    assert "collection(id:$id)" in body["query"]
    assert "documents(limit:$dl)" in body["query"]


@pytest.mark.asyncio
async def test_get_collection_returns_none_when_unknown(monkeypatch):
    _patch(monkeypatch, {"data": {"collection": None}})
    assert await corpus.get_collection("00000000-0000-0000-0000-000000000000") is None


@pytest.mark.asyncio
async def test_get_collection_rejects_bad_uuid_before_network(monkeypatch):
    client = _patch(monkeypatch, {"data": {"collection": None}})
    with pytest.raises(corpus.ToolCallError) as ei:
        await corpus.get_collection("not-a-uuid")
    assert ei.value.kind == "bad_input"
    assert client.calls == []


# --- typed errors -----------------------------------------------------------


class _FailingClient:
    """Fake httpx client whose post raises a transport-level exception."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        raise self._exc


class _StatusResp:
    """Fake response whose raise_for_status raises for the given HTTP status."""

    def __init__(self, status):
        self._status = status

    def raise_for_status(self):
        raise corpus.httpx.HTTPStatusError(
            f"HTTP {self._status}",
            request=corpus.httpx.Request("POST", "http://api/graphql"),
            response=corpus.httpx.Response(self._status),
        )

    def json(self):  # pragma: no cover - raise_for_status always fires first
        return {}


def _patch_failing(monkeypatch, exc):
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: _FailingClient(exc))


def _patch_status(monkeypatch, status):
    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers):
            return _StatusResp(status)

    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: _Client())


@pytest.mark.asyncio
async def test_timeout_maps_to_retriable_upstream_unavailable(monkeypatch):
    _patch_failing(monkeypatch, corpus.httpx.TimeoutException("boom"))
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.full_text_search("x")
    assert err.value.kind == "upstream_unavailable"
    assert err.value.retriable is True


@pytest.mark.asyncio
async def test_connect_error_maps_to_retriable_upstream_unavailable(monkeypatch):
    _patch_failing(monkeypatch, corpus.httpx.ConnectError("no route"))
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.full_text_search("x")
    assert err.value.kind == "upstream_unavailable"
    assert err.value.retriable is True


@pytest.mark.asyncio
async def test_http_401_maps_to_permission_denied(monkeypatch):
    _patch_status(monkeypatch, 401)
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.full_text_search("x")
    assert err.value.kind == "permission_denied"
    assert err.value.retriable is False


@pytest.mark.asyncio
async def test_http_500_maps_to_retriable_upstream_unavailable(monkeypatch):
    _patch_status(monkeypatch, 500)
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.full_text_search("x")
    assert err.value.kind == "upstream_unavailable"
    assert err.value.retriable is True


@pytest.mark.asyncio
async def test_graphql_permission_error_is_typed_and_backward_compatible(monkeypatch):
    _patch(monkeypatch, {"errors": [{"message": "permission denied for table canonical"}]})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.full_text_search("x")
    assert err.value.kind == "permission_denied"
    assert isinstance(err.value, RuntimeError)  # existing callers keep working


@pytest.mark.asyncio
async def test_graphql_invalid_input_classified_bad_input(monkeypatch):
    _patch(monkeypatch, {"errors": [{"message": "invalid input syntax for type uuid"}]})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.full_text_search("x")
    assert err.value.kind == "bad_input"


# --- capability not deployed (vision stack absent on the API host) ----------


@pytest.mark.asyncio
async def test_missing_vision_stack_classified_unsupported(monkeypatch):
    _patch(
        monkeypatch,
        {
            "errors": [
                {
                    "message": (
                        "crossModalImageSearch is unavailable on this deployment: "
                        "vision stack module(s) open_clip are not installed on the API host"
                    )
                }
            ]
        },
    )
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.cross_modal_image_search("infographic with icons")
    assert err.value.kind == "unsupported"
    assert err.value.retriable is False
    assert err.value.hint  # steers agents to text-side tools instead of retrying


@pytest.mark.asyncio
async def test_legacy_open_clip_import_leak_classified_unsupported(monkeypatch):
    # Regression: API hosts predating the typed error leak the raw
    # ModuleNotFoundError text straight through GraphQL.
    _patch(monkeypatch, {"errors": [{"message": "No module named 'open_clip'"}]})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.cross_modal_image_search("diagram")
    assert err.value.kind == "unsupported"
    assert err.value.retriable is False


# --- pre-flight input validation --------------------------------------------


@pytest.mark.asyncio
async def test_malformed_document_id_rejected_before_network(monkeypatch):
    client = _patch(monkeypatch, {"data": {"document": None}})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.get_document("not-a-uuid")
    assert err.value.kind == "bad_input"
    assert client.calls == []  # rejected before any GraphQL round-trip


@pytest.mark.asyncio
async def test_optional_document_id_validated_when_present(monkeypatch):
    client = _patch(monkeypatch, {"data": {"searchSentences": []}})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.search_sentences("term", document_id="nope")
    assert err.value.kind == "bad_input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_blank_term_rejected(monkeypatch):
    client = _patch(monkeypatch, {"data": {"searchSentences": []}})
    with pytest.raises(corpus.ToolCallError) as err:
        await corpus.search_sentences("   ")
    assert err.value.kind == "bad_input"
    assert client.calls == []


@pytest.mark.asyncio
async def test_lexeme_kwic_unwraps_nested_occurrences(monkeypatch):
    _patch(monkeypatch, {"data": {"lexeme": {"uid": "u1", "occurrences": [{"hit": "run"}]}}})
    assert await corpus.lexeme_kwic("u1") == [{"hit": "run"}]


@pytest.mark.asyncio
async def test_lexeme_kwic_handles_missing_lexeme(monkeypatch):
    _patch(monkeypatch, {"data": {"lexeme": None}})
    assert await corpus.lexeme_kwic("u1") == []


@pytest.mark.asyncio
async def test_list_documents_and_entity_frequency_map(monkeypatch):
    _patch(monkeypatch, {"data": {"documents": [{"id": "d1"}]}})
    assert await corpus.list_documents(limit=3, doc_type="pdf") == [{"id": "d1"}]
    _patch(monkeypatch, {"data": {"entityFrequency": [{"text": "Berlin", "count": 3}]}})
    assert await corpus.entity_frequency() == [{"text": "Berlin", "count": 3}]


def test_tools_tuple_is_the_read_tools():
    assert {fn.__name__ for fn in corpus.TOOLS} == {
        "search_sentences",
        "read_document_sentences",
        "full_text_search",
        "list_documents",
        "get_document",
        "get_job",
        "list_collections",
        "get_collection",
        "entity_frequency",
        "lexeme_kwic",
        # Prism visual tools
        "document_images",
        "image_class_summary",
        "cross_modal_image_search",
        "visual_captions",
    }


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
    assert "sentences(limit:$l,after:$a)" in body["query"]
    # every citation handle is selected so slices stay attributable
    for field in ("ordinal", "text", "charStart", "charEnd", "cursor"):
        assert field in body["query"]
    assert body["variables"] == {"id": _DOC, "l": 2, "a": None}


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
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: client)

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
