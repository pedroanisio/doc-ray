"""WikidataClient resilience tests — retry/backoff, pacing, caching (no network).

Uses httpx.MockTransport so the live-fire failure modes (429 rate limits) are
reproduced deterministically. The sleep function is injected, so no test waits.
"""

from __future__ import annotations

import httpx
import pytest

from doc_ray.adapters.wikidata import WikidataClient

_SEARCH_OK = {
    "search": [
        {
            "id": "Q3335",
            "label": "George Orwell",
            "description": "English author",
            "match": {"language": "en", "text": "George Orwell"},
        }
    ]
}

_ENTITY_OK = {
    "entities": {
        "Q3335": {
            "labels": {"en": {"value": "George Orwell"}},
            "aliases": {},
            "descriptions": {"en": {"value": "English author"}},
            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5"}}}}]},
        }
    }
}


class _FakeSleep:
    """Records requested delays instead of sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _scripted_transport(script: list[tuple[int, dict, dict]]):
    """MockTransport that replays (status, json, headers) responses in order."""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = min(state["n"], len(script) - 1)
        state["n"] += 1
        status, payload, headers = script[idx]
        return httpx.Response(status, json=payload, headers=headers)

    return httpx.MockTransport(handler), state


def test_search_retries_a_429_and_succeeds():
    transport, state = _scripted_transport([(429, {}, {}), (200, _SEARCH_OK, {})])
    sleep = _FakeSleep()
    kb = WikidataClient(transport=transport, sleep=sleep, min_interval=0.0)
    hits = kb.search("George Orwell")
    assert [h.kb_id for h in hits] == ["Q3335"]
    assert state["n"] == 2  # one 429, one success
    assert len(sleep.calls) == 1 and sleep.calls[0] > 0  # backed off before retrying


def test_retry_after_header_is_honored():
    transport, _ = _scripted_transport([(429, {}, {"retry-after": "3"}), (200, _SEARCH_OK, {})])
    sleep = _FakeSleep()
    kb = WikidataClient(transport=transport, sleep=sleep, min_interval=0.0)
    kb.search("George Orwell")
    assert sleep.calls == [3.0]


def test_gives_up_after_max_retries():
    transport, state = _scripted_transport([(429, {}, {})])
    kb = WikidataClient(transport=transport, sleep=_FakeSleep(), min_interval=0.0, max_retries=2)
    with pytest.raises(httpx.HTTPStatusError):
        kb.search("George Orwell")
    assert state["n"] == 3  # initial attempt + 2 retries


def test_server_errors_are_retried_too():
    transport, state = _scripted_transport([(503, {}, {}), (200, _SEARCH_OK, {})])
    kb = WikidataClient(transport=transport, sleep=_FakeSleep(), min_interval=0.0)
    assert kb.search("George Orwell")[0].kb_id == "Q3335"
    assert state["n"] == 2


def test_get_is_cached_per_process():
    transport, state = _scripted_transport([(200, _ENTITY_OK, {})])
    kb = WikidataClient(transport=transport, sleep=_FakeSleep(), min_interval=0.0)
    first = kb.get("Q3335")
    second = kb.get("Q3335")
    assert first is not None and second is not None
    assert first.label == second.label == "George Orwell"
    assert state["n"] == 1  # second call served from cache


def test_search_is_cached_per_process():
    transport, state = _scripted_transport([(200, _SEARCH_OK, {})])
    kb = WikidataClient(transport=transport, sleep=_FakeSleep(), min_interval=0.0)
    kb.search("George Orwell", limit=5)
    kb.search("George Orwell", limit=5)
    assert state["n"] == 1


def test_min_interval_paces_consecutive_requests():
    transport, _ = _scripted_transport([(200, _SEARCH_OK, {})])
    sleep = _FakeSleep()
    kb = WikidataClient(transport=transport, sleep=sleep, min_interval=10.0)
    kb.search("a")
    kb.search("b")  # different key -> real second request -> must be paced
    assert any(delay > 8.0 for delay in sleep.calls)
