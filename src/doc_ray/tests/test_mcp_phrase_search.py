"""MCP phrase-search forwarding, citations, and coverage contracts (#45)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from doc_ray.entrypoints.mcp import corpus, server  # noqa: F401

_DOC = "11111111-1111-1111-1111-111111111111"


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Client:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def post(self, url, json, headers):
        self.calls.append((url, json, headers))
        return _Response(self.payload)


def _patch(monkeypatch, payload: dict) -> _Client:
    client = _Client(payload)
    monkeypatch.setattr(corpus, "acquire_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_phrase_search_forwards_mode_and_returns_profile_occurrences_and_scan_coverage(
    monkeypatch,
):
    payload = {
        "data": {
            "searchSentencePage": {
                "hits": [
                    {
                        "id": "1",
                        "documentId": _DOC,
                        "ordinal": 7,
                        "pre": "",
                        "hit": "Algorithm Design",
                        "post": ".",
                        "cursor": "cur",
                        "occurrences": [{"charStart": 140, "charEnd": 156}],
                        "occurrenceCount": 1,
                    }
                ],
                "matchProfile": "surface_word_v1",
                "candidateScanned": 2001,
                "candidateLimit": 2001,
                "candidateLimitReached": True,
            }
        }
    }
    client = _patch(monkeypatch, payload)

    out = await corpus.search_sentences("Algorithm Design", match="phrase", limit=20)

    assert out["matchProfile"] == "surface_word_v1"
    assert out["hits"][0]["occurrences"] == [{"charStart": 140, "charEnd": 156}]
    assert out["coverage"]["candidate_limit_reached"] is True
    assert out["coverage"]["exhaustive"] is False
    assert "phrase" in (out["note"] or "").lower()
    _url, body, _headers = client.calls[0]
    assert body["variables"]["m"] == "PHRASE"
    assert "searchSentencePage" in body["query"]


@pytest.mark.asyncio
async def test_verified_zero_phrase_note_names_the_profile(monkeypatch):
    _patch(
        monkeypatch,
        {
            "data": {
                "searchSentencePage": {
                    "hits": [],
                    "matchProfile": "surface_word_v1",
                    "candidateScanned": 12,
                    "candidateLimit": 2001,
                    "candidateLimitReached": False,
                }
            }
        },
    )

    out = await corpus.search_sentences("algorithm design", match="phrase")

    assert out["coverage"]["exhaustive"] is True
    assert "surface_word_v1" in (out["note"] or "")


@pytest.mark.asyncio
async def test_concise_phrase_search_keeps_occurrence_citations(monkeypatch):
    _patch(
        monkeypatch,
        {
            "data": {
                "searchSentencePage": {
                    "hits": [
                        {
                            "id": "1",
                            "documentId": _DOC,
                            "title": "Algorithm Design",
                            "ordinal": 7,
                            "pre": "Read ",
                            "hit": "Algorithm Design",
                            "post": ".",
                            "cursor": "cur",
                            "occurrences": [{"charStart": 140, "charEnd": 156}],
                            "occurrenceCount": 1,
                        }
                    ],
                    "matchProfile": "surface_word_v1",
                    "candidateScanned": 1,
                    "candidateLimit": 2001,
                    "candidateLimitReached": False,
                }
            }
        },
    )

    out = await corpus.search_sentences(
        "Algorithm Design", match="phrase", response_format="concise"
    )

    assert out["hits"] == [
        {
            "documentId": _DOC,
            "ordinal": 7,
            "pre": "Read ",
            "hit": "Algorithm Design",
            "post": ".",
            "occurrences": [{"charStart": 140, "charEnd": 156}],
            "occurrenceCount": 1,
            "cursor": "cur",
        }
    ]
