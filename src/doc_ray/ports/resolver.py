"""Port: the frontier entity resolver — constrained choice (PALS's Law).

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — LLM OUTPUT IS UNVERIFIED BY DEFAULT.
The resolver NEVER emits a freehand id from model memory. It selects a candidate from
the supplied Stage-2 set, declares a genuinely *new* entity, or *abstains*. The adapter
MUST validate the verdict against the candidate set — a selection that is not in the set
(a hallucinated id) is coerced to ``abstain`` and routed to human review, never committed.
A selection is verifiable; a hallucinated id is not. Fulfilled by
:mod:`doc_ray.adapters.resolver_claude`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class ResolutionKind(str, Enum):
    """The three (and only three) verdicts the resolver may return."""

    SELECT = "select"
    NEW = "new_entity"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class Candidate:
    """A canonical candidate offered to the resolver for a mention."""

    canonical_id: int
    preferred: str
    entity_type: str
    description: str | None = None


@dataclass(frozen=True)
class Resolution:
    """The resolver's constrained verdict for one mention.

    Attributes:
        kind: SELECT, NEW, or ABSTAIN.
        canonical_id: The chosen candidate id — set iff ``kind`` is SELECT and it is a
            member of the offered candidate set.
        confidence: Model-reported confidence in ``[0, 1]``.
        harvested_aliases: Alternate surface forms the resolver observed in context.
        new_preferred: Proposed preferred label — set iff ``kind`` is NEW.
    """

    kind: ResolutionKind
    canonical_id: int | None = None
    confidence: float = 0.0
    harvested_aliases: tuple[str, ...] = field(default_factory=tuple)
    new_preferred: str | None = None


class EntityResolver(Protocol):
    """Resolve a mention-in-context to a candidate, a new entity, or abstention."""

    def resolve(self, mention: str, sentence: str, candidates: list[Candidate]) -> Resolution:
        """Return a constrained verdict; never a freehand identifier.

        Args:
            mention: The mention surface text.
            sentence: The sentence the mention occurs in (disambiguating context).
            candidates: The Stage-2 candidate set the verdict must choose from.

        Returns:
            A :class:`Resolution`; SELECT ids are guaranteed by the adapter to be in
            *candidates*.
        """
        ...
