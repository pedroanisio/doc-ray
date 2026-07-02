"""Wikipedia pageprops client — exact title -> Wikidata QID resolution.

Fuzzy KB search is a seeding trap: top hits drift to the wrong entity
('versal' -> Versailles, bare 'Albertus' -> Albertus Magnus). Resolving through
a Wikipedia *article title* pins identity exactly — the article either is the
entity or it is not — and the ``disambiguation`` pageprop lets us refuse
disambiguation pages outright (the 'Paul Renner' trap) instead of seeding a
Wikimedia-internal item.

Mirrors :class:`doc_ray.adapters.wikidata.WikidataClient`: injectable transport
and sleep for tests, request pacing, retry with backoff on 429/5xx.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from doc_ray.adapters.wikidata import _RETRYABLE_STATUSES, _retry_after_seconds

#: Batch ceiling for ``titles=`` (MediaWiki API limit for anonymous clients).
_BATCH = 50


def parse_pageprops(titles: list[str], data: dict[str, Any]) -> dict[str, str | None]:
    """Map each requested title to its Wikidata QID (pure; ``None`` = no usable match).

    Follows the API's ``normalized`` then ``redirects`` chains to the final page
    title. A page that is missing, lacks a wikibase item, or is flagged as a
    disambiguation page resolves to ``None`` — disambiguation items are
    Wikimedia bookkeeping, never seedable entities.

    Args:
        titles: The titles exactly as requested.
        data: The decoded ``action=query&prop=pageprops`` response.

    Returns:
        ``{requested_title: qid | None}`` for every requested title.
    """
    query = data.get("query", {})
    normalized = {n["from"]: n["to"] for n in query.get("normalized", [])}
    redirected = {r["from"]: r["to"] for r in query.get("redirects", [])}
    qid_by_title: dict[str, str | None] = {}
    for page in query.get("pages", {}).values():
        props = page.get("pageprops", {})
        usable = "missing" not in page and "disambiguation" not in props
        qid_by_title[page["title"]] = props.get("wikibase_item") if usable else None
    out: dict[str, str | None] = {}
    for title in titles:
        final = normalized.get(title, title)
        final = redirected.get(final, final)
        out[title] = qid_by_title.get(final)
    return out


class WikipediaClient:
    """Live Wikipedia pageprops resolver with pacing, retry/backoff, and caching."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        lang: str = "en",
        max_retries: int = 4,
        min_interval: float = 0.5,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Open an HTTP client for the ``{lang}.wikipedia.org`` action API.

        Args:
            timeout: Per-request timeout in seconds.
            lang: Wikipedia language edition to query.
            max_retries: Retries after the initial attempt on 429/5xx.
            min_interval: Minimum spacing between consecutive requests.
            transport: Optional httpx transport (tests inject a MockTransport).
            sleep: Sleep function (tests inject a recorder).
        """
        self._api = f"https://{lang}.wikipedia.org/w/api.php"
        self._max_retries = max_retries
        self._min_interval = min_interval
        self._sleep = sleep
        self._last_request_at = 0.0
        self._cache: dict[str, str | None] = {}
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "doc-ray/canonicalization"},
            transport=transport,
        )

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        """GET the action API with pacing and retry/backoff on 429/5xx."""
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
            self._last_request_at = time.monotonic()

            response = self._client.get(self._api, params=params)
            if response.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                self._sleep(_retry_after_seconds(response, attempt))
                continue
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data
        raise AssertionError("unreachable")  # pragma: no cover

    def qids_for_titles(self, titles: list[str]) -> dict[str, str | None]:
        """Resolve article titles to QIDs (batched, cached per process).

        Args:
            titles: Wikipedia article titles (redirects and normalization are
                followed server-side).

        Returns:
            ``{title: qid | None}`` for every input title.
        """
        out: dict[str, str | None] = {}
        pending = [t for t in titles if t not in self._cache]
        for i in range(0, len(pending), _BATCH):
            chunk = pending[i : i + _BATCH]
            data = self._request(
                {
                    "action": "query",
                    "prop": "pageprops",
                    "ppprop": "wikibase_item|disambiguation",
                    "redirects": 1,
                    "format": "json",
                    "titles": "|".join(chunk),
                }
            )
            self._cache.update(parse_pageprops(chunk, data))
        for title in titles:
            out[title] = self._cache.get(title)
        return out

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
