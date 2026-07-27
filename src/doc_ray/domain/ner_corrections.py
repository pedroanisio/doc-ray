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
    """The span- and surface-scoped ways a human corrects NER output."""

    RECLASSIFY = "reclassify"  # the span is an entity, but the wrong type
    REJECT = "reject"  # the span is not an entity at all
    ADD = "add"  # a real entity the model missed
    ADJUST_SPAN = "adjust_span"  # the entity is real but the boundary is wrong
    RECLASSIFY_SURFACE = "reclassify_surface"  # exact surface + current type
    REJECT_SURFACE = "reject_surface"  # reject exact surface + current type


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
    """A human decision about NER output at one span or an exact surface.

    Span actions carry ``(char_start, char_end)``. Surface actions instead carry
    ``surface`` + ``surface_original_type`` and match both by exact, case-sensitive
    equality. That precision prevents a broad rule from swallowing a different
    casing or a mention already carrying another type.
    """

    action: Action
    char_start: int | None
    char_end: int | None
    entity_type: str | None = None  # required for RECLASSIFY / ADD
    surface: str | None = None  # optional label for ADD
    new_char_start: int | None = None  # required for ADJUST_SPAN
    new_char_end: int | None = None
    surface_original_type: str | None = None  # matcher for surface actions

    def validate(self) -> None:
        """Raise ``ValueError`` if the correction is not applicable as declared."""
        if self.action in (Action.RECLASSIFY_SURFACE, Action.REJECT_SURFACE):
            self._validate_surface_action()
            return
        self._validate_span_action()

    def _validate_surface_action(self) -> None:
        """Validate the exact-match rule shape shared by both surface actions."""
        if self.char_start is not None or self.char_end is not None:
            raise ValueError(f"{self.action.value} applies without a target span")
        if not self.surface or not self.surface.strip():
            raise ValueError(f"{self.action.value} requires a non-empty surface")
        if not self.surface_original_type or not self.surface_original_type.strip():
            raise ValueError(f"{self.action.value} requires an original type matcher")
        if self.action is Action.RECLASSIFY_SURFACE:
            if not self.entity_type:
                raise ValueError("reclassify_surface requires an entity_type")
            if self.entity_type == self.surface_original_type:
                raise ValueError("reclassify_surface requires a different entity_type")

    def _validate_span_action(self) -> None:
        """Preserve the four original span actions' validation guarantees."""
        if self.char_start is None or self.char_end is None:
            raise ValueError(f"{self.action.value} requires a target span")
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
    applying the same corrections again yields the same set. Corrections replay
    chronologically, so a later span exception overrides an earlier surface rule
    and a later surface rule can override an earlier span decision when its exact
    surface + current-type matcher applies.

    Semantics per action (all upsert-style, so a fix is never lost even if the
    model stops emitting the span):

    * ``RECLASSIFY`` — ensure a mention at the span with the corrected type.
    * ``REJECT`` — remove any mention at the span.
    * ``ADD`` — ensure a mention at the span with the given type/surface.
    * ``ADJUST_SPAN`` — move the mention from the old span to the new one,
      preserving its type (or the correction's type if given).
    * ``RECLASSIFY_SURFACE`` — retype every exact surface + original-type match.
    * ``REJECT_SURFACE`` — remove every exact surface + original-type match.
    """
    by_span: dict[tuple[int, int], Mention] = {(m.char_start, m.char_end): m for m in mentions}
    for c in corrections:
        c.validate()
        if c.action in (Action.RECLASSIFY_SURFACE, Action.REJECT_SURFACE):
            assert c.surface is not None and c.surface_original_type is not None
            for key, mention in list(by_span.items()):
                if mention.surface != c.surface or mention.entity_type != c.surface_original_type:
                    continue
                if c.action is Action.REJECT_SURFACE:
                    by_span.pop(key)
                else:
                    assert c.entity_type is not None
                    by_span[key] = Mention(
                        char_start=mention.char_start,
                        char_end=mention.char_end,
                        entity_type=c.entity_type,
                        surface=mention.surface,
                        source="human",
                    )
            continue
        assert c.char_start is not None and c.char_end is not None
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
        else:
            assert c.action is Action.ADJUST_SPAN
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
