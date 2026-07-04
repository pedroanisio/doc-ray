"""spaCy annotation language/model capability registry."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

SUPPORTED_SPACY_LAYERS = (
    "tokenization",
    "lemmatization",
    "part_of_speech",
    "morphology",
    "shallow_chunking",
    "dependency_parsing",
    "named_entity_recognition",
    "coreference_string_match",
    "lexeme_projection",
)

CAPABILITY_MODELS = {"en": "en_core_web_sm", "pt": "pt_core_news_sm"}


@dataclass(frozen=True)
class SpacyCapability:
    """Installed-state view of one language model supported by the spaCy adapter."""

    language: str
    model: str
    installed: bool
    supported_layers: tuple[str, ...]
    fallback_policy: str


@dataclass(frozen=True)
class AnnotationLanguageResolution:
    """Resolved model choice for a detected language and fallback policy."""

    requested_language: str
    annotation_language: str
    model: str
    fallback_policy: str
    fallback_used: bool

    def provenance_params(self) -> dict[str, str | bool]:
        """Return language/model facts to persist with the annotation event."""
        params: dict[str, str | bool] = {
            "detected_language": self.requested_language,
            "annotation_language": self.annotation_language,
            "language_model": self.model,
            "language_fallback_policy": self.fallback_policy,
            "language_fallback_used": self.fallback_used,
        }
        if self.fallback_used:
            params["language_fallback_from"] = self.requested_language
            params["language_fallback_to"] = self.annotation_language
        return params


class UnsupportedAnnotationLanguage(RuntimeError):
    """Raised when the annotator has no configured model for a language."""


def normalize_language(lang: str | None, default: str = "en") -> str:
    """Normalize a language code for capability lookup."""
    value = (lang or default).strip().lower()
    return value or default


def spacy_capabilities(fallback_policy: str = "fail") -> list[SpacyCapability]:
    """Return the spaCy language/model capability registry.

    Args:
        fallback_policy: Runtime fallback policy to report with each capability.

    Returns:
        Capability rows with model names, supported layers, and install status.
    """
    return [
        SpacyCapability(
            language=language,
            model=model,
            installed=importlib.util.find_spec(model) is not None,
            supported_layers=SUPPORTED_SPACY_LAYERS,
            fallback_policy=fallback_policy,
        )
        for language, model in sorted(CAPABILITY_MODELS.items())
    ]
