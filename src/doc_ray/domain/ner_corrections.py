"""NER correction domain — durable human fixes to ML-produced named entities.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — ML OUTPUT IS UNVERIFIED BY DEFAULT

spaCy NER is statistical output: it mislabels types, clips spans, invents
entities, and misses them. Those are properties of the model, not exceptions.
This module is the *verification layer* PURPOSE.md mandates ("AI proposes,
verification decides"): a human (or a reviewing agent, under write scope)
records a correction, and it is re-applied on every future annotation run.

Why corrections cannot live on ``entity_mention``: those rows are ephemeral —
``ON DELETE CASCADE`` from ``annotations`` (migration 010), so every
``reannotate`` deletes and rebuilds them. A fix written there would vanish on
the next run. Instead a correction is keyed by a *stable span identity*
``(char_start, char_end)`` on the document, and :func:`reconcile` re-applies the
whole correction set onto the freshly-produced ML mentions. The function is pure
and deterministic, which is what makes the guarantee — *a correction survives
re-annotation* — provable without a database.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Action(str, enum.Enum):
    """The four ways a human corrects the NER output."""

    RECLASSIFY = "reclassify"  # the span is an entity, but the wrong type
    REJECT = "reject"  # the span is not an entity at all
    ADD = "add"  # a real entity the model missed
    ADJUST_SPAN = "adjust_span"  # the entity is real but the boundary is wrong


@dataclass(frozen=True)
class Mention:
    """A named-entity mention as a resolved span identity.

    ``source`` distinguishes raw model output (``ml``) from a mention a human
    correction produced or altered (``human``) — the provenance the reader and
    the audit trail surface.
    """

    char_start: int
    char_end: int
    entity_type: str
    surface: str
    source: str = "ml"


@dataclass(frozen=True)
class Correction:
    """A human decision about the NER output at a span.

    The target span ``(char_start, char_end)`` identifies what is being
    corrected; the other fields carry the correction's payload per action.
    """

    action: Action
    char_start: int
    char_end: int
    entity_type: str | None = None  # required for RECLASSIFY / ADD
    surface: str | None = None  # optional label for ADD
    new_char_start: int | None = None  # required for ADJUST_SPAN
    new_char_end: int | None = None

    def validate(self) -> None:
        """Raise ``ValueError`` if the correction is not applicable as declared."""
        if self.char_start < 0 or self.char_end < self.char_start:
            raise ValueError(f"invalid target span ({self.char_start}, {self.char_end})")
        if self.action in (Action.RECLASSIFY, Action.ADD) and not self.entity_type:
            raise ValueError(f"{self.action.value} requires an entity_type")
        if self.action is Action.ADJUST_SPAN:
            if self.new_char_start is None or self.new_char_end is None:
                raise ValueError("adjust_span requires a new span (new_char_start/new_char_end)")
            if self.new_char_start < 0 or self.new_char_end < self.new_char_start:
                raise ValueError(f"invalid new span ({self.new_char_start}, {self.new_char_end})")


def reconcile(mentions: list[Mention], corrections: list[Correction]) -> list[Mention]:
    """Re-apply ``corrections`` onto freshly-produced ML ``mentions``.

    Deterministic and idempotent: the result depends only on the inputs, and
    applying the same corrections again yields the same set. Later corrections
    for the same target span win, so a correction log replays cleanly.

    Semantics per action (all upsert-style, so a fix is never lost even if the
    model stops emitting the span):

    * ``RECLASSIFY`` — ensure a mention at the span with the corrected type.
    * ``REJECT`` — remove any mention at the span.
    * ``ADD`` — ensure a mention at the span with the given type/surface.
    * ``ADJUST_SPAN`` — move the mention from the old span to the new one,
      preserving its type (or the correction's type if given).
    """
    by_span: dict[tuple[int, int], Mention] = {(m.char_start, m.char_end): m for m in mentions}
    for c in corrections:
        c.validate()
        span = (c.char_start, c.char_end)
        if c.action is Action.REJECT:
            by_span.pop(span, None)
        elif c.action is Action.RECLASSIFY:
            assert c.entity_type is not None  # validate() guarantees this
            existing = by_span.get(span)
            by_span[span] = Mention(
                char_start=c.char_start,
                char_end=c.char_end,
                entity_type=c.entity_type,
                surface=c.surface or (existing.surface if existing else ""),
                source="human",
            )
        elif c.action is Action.ADD:
            assert c.entity_type is not None  # validate() guarantees this
            by_span[span] = Mention(
                char_start=c.char_start,
                char_end=c.char_end,
                entity_type=c.entity_type,
                surface=c.surface or "",
                source="human",
            )
        elif c.action is Action.ADJUST_SPAN:
            assert c.new_char_start is not None and c.new_char_end is not None  # validate()
            existing = by_span.pop(span, None)
            by_span[(c.new_char_start, c.new_char_end)] = Mention(
                char_start=c.new_char_start,
                char_end=c.new_char_end,
                entity_type=c.entity_type or (existing.entity_type if existing else "MISC"),
                surface=c.surface or (existing.surface if existing else ""),
                source="human",
            )
    return [by_span[k] for k in sorted(by_span)]
