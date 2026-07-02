"""Wikidata knowledge-base adapter (live public API over httpx).

Fulfils :class:`doc_ray.ports.knowledge_base.KnowledgeBase`. Parsing is split into pure
functions (:func:`parse_search`, :func:`parse_entity`) so it is unit-testable without the
network; the live HTTP calls are thin wrappers over them.

Resilience (added after live-fire 429s from Wikidata):
- **Retry with backoff** on 429 and 5xx, honouring a numeric ``Retry-After`` header;
  exponential backoff otherwise, up to ``max_retries``.
- **Pacing** — consecutive requests are spaced at least ``min_interval`` seconds apart.
- **Per-process caching** of ``get`` (by id) and ``search`` (by term+limit), so the
  seed and Stage-2 fallback never re-fetch an entity within a run.
The transport and sleep function are injectable for deterministic tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from doc_ray.ports.knowledge_base import KbEntry

_API = "https://www.wikidata.org/w/api.php"

_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 30.0
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# instance-of (P31) target Q-id -> doc-ray entity class. Extend as coverage grows.
_TYPE_MAP: dict[str, str] = {
    "Q5": "PERSON",
    "Q515": "PLACE",  # city
    "Q6256": "PLACE",  # country
    "Q486972": "PLACE",  # human settlement
    "Q82794": "PLACE",  # geographic region
    "Q43229": "ORG",  # organization
    "Q4830453": "ORG",  # business
    "Q1656682": "EVENT",
    "Q1190554": "EVENT",  # occurrence
}


def _entity_type_from_claims(entity: dict[str, Any]) -> str:
    """Map an entity's P31 (instance-of) claims to a doc-ray class, or 'UNKNOWN'."""
    for stmt in entity.get("claims", {}).get("P31", []):
        try:
            qid = stmt["mainsnak"]["datavalue"]["value"]["id"]
        except (KeyError, TypeError):
            continue
        if qid in _TYPE_MAP:
            return _TYPE_MAP[qid]
    return "UNKNOWN"


def parse_search(data: dict[str, Any]) -> list[KbEntry]:
    """Parse a ``wbsearchentities`` response into candidate entries (type unresolved)."""
    entries: list[KbEntry] = []
    for item in data.get("search", []):
        match = item.get("match", {})
        entries.append(
            KbEntry(
                kb_id=item.get("id", ""),
                label=item.get("label") or match.get("text", ""),
                entity_type="",  # search does not carry the type; resolved via get()
                description=item.get("description"),
                lang=match.get("language"),
            )
        )
    return entries


def parse_entity(qid: str, data: dict[str, Any]) -> KbEntry | None:
    """Parse a ``wbgetentities`` response for *qid* into a fully-typed :class:`KbEntry`."""
    entity = data.get("entities", {}).get(qid)
    if not entity:
        return None
    labels = entity.get("labels", {})
    pref_lang = "en" if "en" in labels else next(iter(labels), None)
    label = labels.get(pref_lang, {}).get("value", qid) if pref_lang else qid
    # Aliases: every alias in every language, plus non-preferred labels (cross-lingual forms).
    aliases = tuple(
        a["value"] for lang_aliases in entity.get("aliases", {}).values() for a in lang_aliases
    ) + tuple(v["value"] for lang, v in labels.items() if lang != pref_lang)
    descriptions = entity.get("descriptions", {})
    description = descriptions.get("en", {}).get("value") if "en" in descriptions else None
    return KbEntry(
        kb_id=qid,
        label=label,
        entity_type=_entity_type_from_claims(entity),
        description=description,
        lang=pref_lang,
        aliases=aliases,
    )


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    """Delay before the next attempt: numeric Retry-After, else exponential backoff."""
    header = response.headers.get("retry-after", "")
    try:
        return max(0.0, float(header))
    except ValueError:  # absent or HTTP-date form -> exponential backoff
        return min(_BACKOFF_BASE_SECONDS * 2.0**attempt, _BACKOFF_CAP_SECONDS)


class WikidataClient:
    """Live Wikidata :class:`KnowledgeBase` with retry/backoff, pacing, and caching."""

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
        """Open an HTTP client for the Wikidata action API.

        Args:
            timeout: Per-request timeout in seconds.
            lang: Search language.
            max_retries: Retries after the initial attempt on 429/5xx.
            min_interval: Minimum spacing between consecutive requests (politeness).
            transport: Optional httpx transport (tests inject a MockTransport).
            sleep: Sleep function (tests inject a recorder).
        """
        self._lang = lang
        self._max_retries = max_retries
        self._min_interval = min_interval
        self._sleep = sleep
        self._last_request_at = 0.0
        self._entity_cache: dict[str, KbEntry | None] = {}
        self._search_cache: dict[tuple[str, int], list[KbEntry]] = {}
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

            response = self._client.get(_API, params=params)
            if response.status_code in _RETRYABLE_STATUSES and attempt < self._max_retries:
                self._sleep(_retry_after_seconds(response, attempt))
                continue
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data
        raise AssertionError("unreachable")  # pragma: no cover

    def search(
        self, label: str, entity_type: str | None = None, *, limit: int = 10
    ) -> list[KbEntry]:
        """Search Wikidata for items matching *label* (cached per process)."""
        key = (label, limit)
        if key in self._search_cache:
            return self._search_cache[key]
        data = self._request(
            {
                "action": "wbsearchentities",
                "search": label,
                "language": self._lang,
                "type": "item",
                "limit": limit,
                "format": "json",
            }
        )
        hits = parse_search(data)
        self._search_cache[key] = hits
        return hits

    def get(self, kb_id: str) -> KbEntry | None:
        """Fetch one entity by Q-id (cached per process), or ``None`` if unknown."""
        if kb_id in self._entity_cache:
            return self._entity_cache[kb_id]
        data = self._request(
            {
                "action": "wbgetentities",
                "ids": kb_id,
                "props": "labels|aliases|descriptions|claims",
                "format": "json",
            }
        )
        entry = parse_entity(kb_id, data)
        self._entity_cache[kb_id] = entry
        return entry

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
