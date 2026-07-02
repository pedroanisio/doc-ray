"""Port: an external knowledge base of entities (e.g. Wikidata).

Used to seed the canonical registry (Stage 0) and to generate candidates for mentions
the remissive index cannot resolve (Stage 2.3). Fulfilled by
:mod:`doc_ray.adapters.wikidata`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class KbEntry:
    """A knowledge-base entity with its multilingual labels/aliases.

    Attributes:
        kb_id: KB identifier (e.g. Wikidata ``Q188817``).
        label: Preferred label.
        entity_type: KB entity type mapped to a doc-ray class (PERSON/PLACE/...).
        description: Short gloss, if any.
        lang: Primary language code of the label, if known.
        aliases: Alternate surface forms (any language).
    """

    kb_id: str
    label: str
    entity_type: str
    description: str | None = None
    lang: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


class KnowledgeBase(Protocol):
    """Search and fetch canonical entities from an external KB."""

    def search(
        self, label: str, entity_type: str | None = None, *, limit: int = 10
    ) -> list[KbEntry]:
        """Return candidate entries matching *label*, optionally filtered by type."""
        ...

    def get(self, kb_id: str) -> KbEntry | None:
        """Fetch one entry (with aliases/labels) by id, or ``None`` if unknown."""
        ...
