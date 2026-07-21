"""MCP corpus tools forward the caller's bearer token to GraphQL and map the result (no network)."""

from __future__ import annotations

import pytest

from doc_ray.entrypoints.mcp import corpus


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
    assert await corpus.get_document("x") is None


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
        "full_text_search",
        "list_documents",
        "get_document",
        "entity_frequency",
        "lexeme_kwic",
        # Prism visual tools
        "document_images",
        "image_class_summary",
        "cross_modal_image_search",
        "visual_captions",
    }
