"""Genre → resolution-scope policy (issue #6).

This module answers one pure question: given a document's **genre** and an entity
**type**, does that entity resolve inside the work's own lens (``LOCAL``) or against
the shared, cross-document identity space (``GLOBAL``)?

The library holds fiction and non-fiction together, and they demand opposite
identity rules. In a novel, *Duncan Idaho* belongs to that work; in a history,
*Paris* is the same Paris as in every other history. Applying one rule to both
either fragments real-world knowledge (destroying reuse) or merges fictional
characters across unrelated books (fabricating identity).

Two design commitments make this safe:

* **Conservative default (PALS's Law).** The genre label is untrusted ML/heuristic
  output. Anything but *confirmed fiction* resolves ``GLOBAL``: ``non_fiction``,
  ``mixed``, ``unknown``, and any entity type not on the fiction-local list. The
  reasoning is asymmetric — a wrong GLOBAL link is *visible and repairable* (two
  entities merged that shouldn't be, a human unmerges them), whereas a wrong LOCAL
  fragmentation is *silent* (the same real-world entity split across works, and
  nobody sees the loss of reuse). We prefer the loud, fixable error.
* **The type partition is data, not scattered conditionals** — two frozensets that
  can be reviewed and diffed. Measurements and calendar facts (``DATE``, ``MONEY``,
  …) are never fictional even inside a novel, so they stay ``GLOBAL`` there too.

This block *defines and exposes* the policy; it does not apply it. The projection engine (issue #8),
the projection engine, consumes :func:`resolution_scope` — nothing in entity
resolution changes here.
"""

from __future__ import annotations

import enum


class Genre(str, enum.Enum):
    """The genre axis of a document, orthogonal to its file format (``doc_type``)."""

    FICTION = "fiction"
    NON_FICTION = "non_fiction"
    MIXED = "mixed"  # anthology, historical novel — genuinely both (routed GLOBAL)
    UNKNOWN = "unknown"


class ResolutionScope(str, enum.Enum):
    """Where an entity's identity is resolved."""

    LOCAL = "local"  # inside the work's own lens (fictional entities)
    GLOBAL = "global"  # against the shared cross-document identity space


#: Entity types that, *inside a fiction work*, belong to that work rather than the
#: shared world. Verified against the annotator's label inventory (spaCy/OntoNotes
#: labels emitted into ``entity_mention.entity_type``).
FICTION_LOCAL_TYPES: frozenset[str] = frozenset(
    {
        "PERSON",
        "ORG",
        "GPE",
        "LOC",
        "FAC",
        "NORP",
        "EVENT",
        "WORK_OF_ART",
        "PRODUCT",
    }
)

#: Entity types that are never fictional — measurements and calendar facts stay
#: GLOBAL even inside a novel. Kept explicit (rather than "everything not local")
#: so the two partitions can be reviewed together and asserted disjoint.
ALWAYS_GLOBAL_TYPES: frozenset[str] = frozenset(
    {
        "DATE",
        "TIME",
        "MONEY",
        "PERCENT",
        "QUANTITY",
        "ORDINAL",
        "CARDINAL",
        "LANGUAGE",
        "LAW",  # a named statute is a real-world referent even when cited in a novel
    }
)


def resolution_scope(genre: Genre, entity_type: str) -> ResolutionScope:
    """Return where an entity of *entity_type* resolves in a *genre* document.

    Total and deterministic over every ``(Genre, str)`` input:

    * ``fiction`` + a fiction-local type  → ``LOCAL``.
    * everything else                     → ``GLOBAL`` (the conservative default):
      ``non_fiction``/``mixed``/``unknown`` for any type, and — inside fiction —
      always-global types and any unrecognized label.

    Args:
        genre: The document's genre label (untrusted until confirmed).
        entity_type: The mention's entity type; matched case-insensitively so a
            ``'person'`` row and a ``'PERSON'`` row route identically.

    Returns:
        ``ResolutionScope.LOCAL`` only for a fiction-local type in a fiction
        document; ``ResolutionScope.GLOBAL`` otherwise.
    """
    if genre is Genre.FICTION and entity_type.strip().upper() in FICTION_LOCAL_TYPES:
        return ResolutionScope.LOCAL
    return ResolutionScope.GLOBAL
