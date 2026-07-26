"""Reconcile raw NER frequency rows into one profile per entity (BL-146).

Automated NER splits a single real-world entity across types and spellings. In
the live corpus ``Arrakis`` is tagged LOC 225×, GPE 150×, PERSON 95×, and NORP
76× — four rows that an agent reading ``entity_frequency`` must merge by hand
before it can say anything about a place.

This module does that merge, and does it *honestly*. Rows are grouped by
:func:`~doc_ray.domain.canonical_norm.form_norm` — the same key the ``form_index``
remissive index uses, so the grouping agrees with the canonical registry instead
of inventing a second notion of sameness — and the result carries the full type
spread plus a consistency ratio.

``type_consistency`` is arithmetic, not a model score: the dominant type's share
of the entity's mentions. A value near 1.0 means the annotator agreed with
itself; ``Arrakis`` scores 0.41, which is the signal that the type is not to be
trusted. Nothing here decides what an entity *is* — it reports what the annotator
produced and how much it disagreed with itself, which is
`PALS's Law <../../CLAUDE.md>`_ applied at the read surface.

Only normalization-equivalent spellings fold together. ``Duncan`` and ``Duncan
Idaho`` stay separate: relating them is coreference, and merging them here would
be fabrication.

Pure and deterministic — no I/O, no database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from doc_ray.domain.canonical_norm import form_norm

#: Type recorded when the annotator produced a mention with no usable label.
UNKNOWN_TYPE = "UNKNOWN"


def dominant_type_share(type_counts: Mapping[str, int]) -> float:
    """Return the dominant type's share of the total — the ``type_consistency`` ratio.

    ``max(count) / sum(count)`` in ``[0, 1]``: the same measured arithmetic
    :class:`EntityProfile` uses (a value near 1.0 means the annotator agreed with
    itself; low means it contradicted itself). Shared so consumers that already hold
    a ``{type: count}`` map — e.g. the per-lens entity rollup (Block F) — reuse the
    definition instead of reinventing it. ``0.0`` for an empty map.
    """
    total = sum(type_counts.values())
    if total <= 0:
        return 0.0
    return max(type_counts.values()) / total


@dataclass(frozen=True)
class TypeCount:
    """How many times one entity type was assigned to an entity."""

    entity_type: str
    count: int


@dataclass(frozen=True)
class EntityProfile:
    """One reconciled entity: its mentions, the type spread, and how coherent it is.

    Attributes:
        surface: The most frequent spelling, used as the display name.
        normalized_form: The ``form_norm`` grouping key (also the ``form_index`` key).
        mentions: Total mentions across every type and spelling in the group.
        dominant_type: The most frequently assigned type.
        type_spread: Every assigned type with its count, most frequent first.
        type_consistency: ``dominant type count / mentions`` — a measured ratio in
            ``[0, 1]``, never a model confidence. Low means the annotator
            contradicted itself and the type should not be relied on.
        surface_forms: Every distinct spelling folded into this profile.
    """

    surface: str
    normalized_form: str
    mentions: int
    dominant_type: str
    type_spread: tuple[TypeCount, ...]
    type_consistency: float
    surface_forms: tuple[str, ...]


def reconcile(
    rows: Iterable[Mapping[str, Any]], *, limit: int | None = None
) -> list[EntityProfile]:
    """Fold ``{text, entity_type, count}`` rows into profiles, most mentioned first.

    Args:
        rows: Raw frequency rows as returned by the ``entity_frequency`` read path.
            Blank surfaces are dropped; a missing ``count`` counts as zero.
        limit: Maximum profiles to return. Applied **after** grouping — truncating
            the raw rows first would drop half of a split entity and report a
            total that is simply wrong.

    Returns:
        Profiles ordered by descending mentions, ties broken on
        ``normalized_form`` so the ordering is stable across calls.
    """
    groups: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
    for row in rows:
        surface = str(row.get("text") or "").strip()
        if not surface:
            continue
        key = form_norm(surface)
        if not key:
            continue
        entity_type = str(row.get("entity_type") or "").strip() or UNKNOWN_TYPE
        count = int(row.get("count") or 0)
        types, surfaces = groups.setdefault(key, ({}, {}))
        types[entity_type] = types.get(entity_type, 0) + count
        surfaces[surface] = surfaces.get(surface, 0) + count

    profiles: list[EntityProfile] = []
    for key, (types, surfaces) in groups.items():
        mentions = sum(types.values())
        spread = tuple(
            TypeCount(entity_type, count)
            for entity_type, count in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        dominant = spread[0]
        profiles.append(
            EntityProfile(
                surface=min(surfaces.items(), key=lambda kv: (-kv[1], kv[0]))[0],
                normalized_form=key,
                mentions=mentions,
                dominant_type=dominant.entity_type,
                type_spread=spread,
                type_consistency=(dominant.count / mentions) if mentions else 0.0,
                surface_forms=tuple(sorted(surfaces)),
            )
        )
    profiles.sort(key=lambda profile: (-profile.mentions, profile.normalized_form))
    return profiles[:limit] if limit is not None else profiles
