"""Wikidata knowledge-base adapter (live public API over httpx).

Fulfils :class:`doc_ray.ports.knowledge_base.KnowledgeBase`. Parsing is split into pure
functions (:func:`parse_search`, :func:`parse_entity`) so it is unit-testable without the
network; the live HTTP calls are thin wrappers over them.
"""

from __future__ import annotations

from typing import Any

import httpx

from doc_ray.ports.knowledge_base import KbEntry

_API = "https://www.wikidata.org/w/api.php"

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


class WikidataClient:
    """Live Wikidata :class:`KnowledgeBase` over the public action API."""

    def __init__(self, *, timeout: float = 10.0, lang: str = "en") -> None:
        """Open an HTTP client for the Wikidata action API."""
        self._lang = lang
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": "doc-ray/canonicalization"}
        )

    def search(
        self, label: str, entity_type: str | None = None, *, limit: int = 10
    ) -> list[KbEntry]:
        """Search Wikidata for items matching *label* (type filtering happens in get())."""
        resp = self._client.get(
            _API,
            params={
                "action": "wbsearchentities",
                "search": label,
                "language": self._lang,
                "type": "item",
                "limit": limit,
                "format": "json",
            },
        )
        resp.raise_for_status()
        return parse_search(resp.json())

    def get(self, kb_id: str) -> KbEntry | None:
        """Fetch one entity (labels/aliases/descriptions/P31) by Q-id, or ``None``."""
        resp = self._client.get(
            _API,
            params={
                "action": "wbgetentities",
                "ids": kb_id,
                "props": "labels|aliases|descriptions|claims",
                "format": "json",
            },
        )
        resp.raise_for_status()
        return parse_entity(kb_id, resp.json())

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()
