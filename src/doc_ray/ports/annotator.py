"""Port: assign (lemma, POS) to a sequence of word surfaces."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Annotator(Protocol):
    """Analyze pre-tokenized word surfaces into (lemma, upos) pairs.

    Fulfilled by ``doc_ray.domain.lexemes.RuleAnalyzer`` (zero-dependency) and
    ``doc_ray.adapters.annotation_spacy.SpacyAnnotator`` (real POS via spaCy).

    Attributes:
        name: Identifier of the concrete analyzer (recorded in provenance).
    """

    name: str

    def analyze(self, surfaces: list[str]) -> list[tuple[str, str]]:
        """Analyze word surfaces.

        Args:
            surfaces: Word surfaces in reading order, already tokenized.

        Returns:
            One ``(lemma, upos)`` pair per input surface, in the same order.
        """
        ...
