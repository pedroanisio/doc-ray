"""Pure validation tests for spaCy annotation output."""

from __future__ import annotations

import pytest

from doc_ray.adapters.annotation_capabilities import (
    CAPABILITY_MODELS,
    AnnotationLanguageResolution,
)
from doc_ray.adapters.annotation_validation import (
    AnnotationValidationError,
    validate_spacy_annotation_output,
)


def _language_resolution(model: str | None = None) -> AnnotationLanguageResolution:
    # Read the model from the capability registry rather than hard-coding it: the
    # English default moved sm -> trf and a literal here turned a routine model
    # swap into a red suite, while the mismatch branch it was meant to exercise
    # stayed uncovered (see the dedicated rejection test below).
    return AnnotationLanguageResolution(
        requested_language="en",
        annotation_language="en",
        model=model or CAPABILITY_MODELS["en"],
        fallback_policy="fail",
        fallback_used=False,
    )


def _valid_payload():
    return {
        "text_length": len("Alpha beta."),
        "span_map": {(0, 5): "span-alpha", (6, 10): "span-beta"},
        "sentence_ids": {"sent-1"},
        "lang": "en",
        "tokens": [
            ("tok-alpha", "span-alpha", "sent-1", 0, "lex-alpha", "PROPN", "NNP"),
            ("tok-beta", "span-beta", "sent-1", 1, "lex-beta", "NOUN", "NN"),
        ],
        "feats": [("tok-beta", "Number", "Sing")],
        "deps": [("tok-alpha", None, "root"), ("tok-beta", "tok-alpha", "dep")],
        "ents": [("ent-alpha", "span-alpha", "PERSON", "Alpha")],
        "chunks": [("chunk-beta", "span-beta", "NP", "beta")],
        "coref": {"alpha": ["span-alpha"]},
        "lex_occ": [("span-alpha", "lex-alpha"), ("span-beta", "lex-beta")],
        "language_resolution": _language_resolution(),
    }


def test_validate_spacy_annotation_output_accepts_consistent_rows():
    result = validate_spacy_annotation_output(**_valid_payload())

    assert result == {"validation_status": "passed", "validation_errors": 0}


def test_validate_spacy_annotation_output_rejects_model_outside_the_registry():
    """A run whose model is not the registry's model for the language is not a corpus fact."""
    payload = _valid_payload()
    payload["language_resolution"] = _language_resolution(model="en_core_web_sm")

    with pytest.raises(AnnotationValidationError, match="annotation model mismatch"):
        validate_spacy_annotation_output(**payload)


def test_validate_spacy_annotation_output_rejects_out_of_bounds_spans():
    payload = _valid_payload()
    payload["span_map"] = {(0, 500): "span-alpha"}

    with pytest.raises(AnnotationValidationError, match="outside document bounds"):
        validate_spacy_annotation_output(**payload)


def test_validate_spacy_annotation_output_rejects_cross_run_dependency_heads():
    payload = _valid_payload()
    payload["deps"] = [("tok-beta", "foreign-token", "dep")]

    with pytest.raises(AnnotationValidationError, match="unknown dependency head"):
        validate_spacy_annotation_output(**payload)


def test_validate_spacy_annotation_output_rejects_lexeme_projection_drift():
    payload = _valid_payload()
    payload["lex_occ"] = [("span-alpha", "lex-alpha")]

    with pytest.raises(AnnotationValidationError, match="lexeme projection"):
        validate_spacy_annotation_output(**payload)
