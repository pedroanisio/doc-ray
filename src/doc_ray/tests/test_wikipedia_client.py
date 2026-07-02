"""Wikipedia pageprops resolver tests (pure parsing + MockTransport client).

Title -> QID resolution via `prop=pageprops` is the reliable seeding path (exact
article identity, no fuzzy-search top-hit traps). The parser must follow the
normalized -> redirect -> page chain, surface disambiguation pages as
non-matches (the 'Paul Renner' trap), and return None for missing pages.
"""

from __future__ import annotations

import json

import httpx

from doc_ray.adapters.wikipedia import WikipediaClient, parse_pageprops


def _payload(pages, normalized=None, redirects=None):
    query = {"pages": pages}
    if normalized:
        query["normalized"] = normalized
    if redirects:
        query["redirects"] = redirects
    return {"query": query}


def test_parse_pageprops_direct_hit():
    data = _payload({"1": {"title": "Helvetica", "pageprops": {"wikibase_item": "Q504745"}}})
    assert parse_pageprops(["Helvetica"], data) == {"Helvetica": "Q504745"}


def test_parse_pageprops_follows_normalization_and_redirect():
    data = _payload(
        {"7": {"title": "Comic Sans", "pageprops": {"wikibase_item": "Q868492"}}},
        normalized=[{"from": "comic sans", "to": "Comic sans"}],
        redirects=[{"from": "Comic sans", "to": "Comic Sans"}],
    )
    assert parse_pageprops(["comic sans"], data) == {"comic sans": "Q868492"}


def test_parse_pageprops_disambiguation_page_is_no_match():
    # 'Paul Renner' resolves to a disambiguation page whose wikibase item is a
    # Wikimedia-internal entity — treating it as a seed poisons the registry.
    data = _payload(
        {
            "9": {
                "title": "Paul Renner",
                "pageprops": {"wikibase_item": "Q61428304", "disambiguation": ""},
            }
        }
    )
    assert parse_pageprops(["Paul Renner"], data) == {"Paul Renner": None}


def test_parse_pageprops_missing_page_is_none():
    data = _payload({"-1": {"title": "Zibulate", "missing": ""}})
    assert parse_pageprops(["Zibulate"], data) == {"Zibulate": None}


def test_client_resolves_batch_via_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "en.wikipedia.org" in str(request.url)
        titles = request.url.params["titles"].split("|")
        pages = {
            str(i): {"title": t, "pageprops": {"wikibase_item": f"Q{100 + i}"}}
            for i, t in enumerate(titles)
        }
        return httpx.Response(200, text=json.dumps(_payload(pages)))

    client = WikipediaClient(transport=httpx.MockTransport(handler), sleep=lambda s: None)
    try:
        out = client.qids_for_titles(["Helvetica", "Kerning"])
    finally:
        client.close()
    assert out == {"Helvetica": "Q100", "Kerning": "Q101"}


def test_client_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            text=json.dumps(
                _payload({"1": {"title": "Serif", "pageprops": {"wikibase_item": "Q243449"}}})
            ),
        )

    slept: list[float] = []
    client = WikipediaClient(transport=httpx.MockTransport(handler), sleep=slept.append)
    try:
        out = client.qids_for_titles(["Serif"])
    finally:
        client.close()
    assert out == {"Serif": "Q243449"}
    assert calls["n"] == 2
