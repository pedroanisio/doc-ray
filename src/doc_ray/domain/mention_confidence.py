"""Explainable heuristic confidence for prioritizing NER mention review.

spaCy's default transition-based NER does not expose a calibrated per-entity
probability.  This module therefore combines facts Doc-Ray already measures into
a deterministic confidence value used only for ordering a review queue.  It is
not a probability and never decides or mutates what an entity is.

The output is confidence-shaped for future model-score compatibility: ``1.0``
means no heuristic uncertainty was observed and ``0.0`` means the accumulated
penalties exhausted confidence.  Consumers rank ascending so the least-trusted
mentions appear first, always beside the human-readable reasons.

Pure and deterministic — no I/O, database, or model dependency.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from doc_ray.domain.entity_profiles import dominant_type_share
from doc_ray.domain.ner_corrections import Mention

# Type disagreement is the strongest single signal because it is repeated,
# measured evidence that the annotator contradicts itself for the same entity.
TYPE_DISAGREEMENT_MAX_PENALTY = 0.35
# An unresolved surface lacks the corroboration supplied by the curated registry.
UNRESOLVED_PENALTY = 0.25
# Rarity is useful but weaker than disagreement: legitimate entities can occur once.
RARITY_MAX_PENALTY = 0.20
RARITY_TRUSTED_COUNT = 20
# Shape signals are deliberately small; acronyms and stylized names are legitimate.
ALL_CAPS_PENALTY = 0.05
PERSON_DIGIT_PENALTY = 0.10
SINGLE_CHARACTER_PENALTY = 0.15
# Crossing a sentence boundary violates the normal containment shape of an entity span.
CROSS_SENTENCE_PENALTY = 0.25

NO_SIGNALS_REASON = "no heuristic uncertainty signals detected"


@dataclass(frozen=True)
class Signal:
    """One explainable confidence penalty observed for a mention.

    Attributes:
        name: Stable machine name used to deduplicate repeated observations.
        penalty: Amount subtracted from confidence, constrained to ``[0, 1]``.
        reason: Human-readable explanation returned with the score.
    """

    name: str
    penalty: float
    reason: str

    def __post_init__(self) -> None:
        """Reject malformed signals before they can corrupt a review rank."""
        if not self.name.strip():
            raise ValueError("signal name must not be blank")
        if not 0.0 <= self.penalty <= 1.0:
            raise ValueError("signal penalty must be between 0 and 1")
        if not self.reason.strip():
            raise ValueError("signal reason must not be blank")


def type_disagreement_signal(type_counts: Mapping[str, int]) -> Signal | None:
    """Return the penalty implied by the shared dominant-type consistency ratio."""
    consistency = dominant_type_share(type_counts)
    disagreement = 1.0 - consistency
    if disagreement <= 0.0:
        return None
    percent = round(disagreement * 100)
    return Signal(
        name="type_disagreement",
        penalty=TYPE_DISAGREEMENT_MAX_PENALTY * disagreement,
        reason=f"entity type disagrees across {percent}% of its mentions",
    )


def registry_novelty_signal(*, resolved: bool) -> Signal | None:
    """Return a penalty when the surface is absent from the canonical registry."""
    if resolved:
        return None
    return Signal(
        name="registry_novelty",
        penalty=UNRESOLVED_PENALTY,
        reason="surface is unresolved in the canonical registry",
    )


def rarity_signal(*, occurrence_count: int) -> Signal | None:
    """Return a linearly declining rarity penalty below the trusted count floor."""
    if occurrence_count < 0:
        raise ValueError("occurrence_count must not be negative")
    if occurrence_count >= RARITY_TRUSTED_COUNT:
        return None
    bounded_count = max(1, occurrence_count)
    remaining = RARITY_TRUSTED_COUNT - bounded_count
    penalty = RARITY_MAX_PENALTY * remaining / (RARITY_TRUSTED_COUNT - 1)
    if occurrence_count == 0:
        reason = "surface has no counted corpus occurrences"
    elif occurrence_count == 1:
        reason = "surface occurs only once in the corpus"
    else:
        reason = f"surface is rare ({occurrence_count} corpus occurrences)"
    return Signal(name="rarity", penalty=penalty, reason=reason)


def sentence_boundary_signal(*, crosses_boundary: bool) -> Signal | None:
    """Return a penalty when a mention span crosses its containing sentence."""
    if not crosses_boundary:
        return None
    return Signal(
        name="sentence_boundary",
        penalty=CROSS_SENTENCE_PENALTY,
        reason="span crosses a sentence boundary",
    )


def _shape_signals(mention: Mention) -> list[Signal]:
    """Derive local surface-shape observations from ``mention``."""
    surface = mention.surface.strip()
    signals: list[Signal] = []
    if len(surface) == 1:
        signals.append(
            Signal("single_character", SINGLE_CHARACTER_PENALTY, "surface is a single character")
        )
    elif any(character.isalpha() for character in surface) and surface.isupper():
        signals.append(Signal("all_caps", ALL_CAPS_PENALTY, "surface is all uppercase"))
    if mention.entity_type.strip().upper() == "PERSON" and any(
        character.isdigit() for character in surface
    ):
        signals.append(
            Signal("person_digit", PERSON_DIGIT_PENALTY, "PERSON surface contains a digit")
        )
    return signals


def score(mention: Mention, signals: Iterable[Signal | None]) -> tuple[float, list[str]]:
    """Return heuristic confidence and stable explanations for ``mention``.

    Repeated observations with the same name are counted once at their strongest
    penalty.  This makes the result independent of query row order and prevents a
    duplicated join row from amplifying uncertainty.

    Args:
        mention: The NER mention whose local shape is inspected.
        signals: External corpus observations. ``None`` values are accepted so
            callers can pass optional signal-builder results directly.

    Returns:
        ``(confidence, reasons)`` where confidence is clamped to ``[0, 1]`` and
        reasons is deterministically ordered and never empty.  The value is a
        triage rank, not a calibrated probability.
    """
    strongest: dict[str, Signal] = {}
    for signal in [*signals, *_shape_signals(mention)]:
        if signal is None:
            continue
        current = strongest.get(signal.name)
        if current is None or (signal.penalty, signal.reason) > (
            current.penalty,
            current.reason,
        ):
            strongest[signal.name] = signal

    ordered = [strongest[name] for name in sorted(strongest)]
    confidence = max(0.0, min(1.0, 1.0 - sum(signal.penalty for signal in ordered)))
    reasons = [signal.reason for signal in ordered]
    return confidence, reasons or [NO_SIGNALS_REASON]
