"""Pure structural validation for persisted annotation rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set

from doc_ray.adapters.annotation_capabilities import (
    CAPABILITY_MODELS,
    AnnotationLanguageResolution,
)

TokenRow = tuple[str, str, str | None, int, str | None, str, str]
FeatureRow = tuple[str, str, str]
DependencyRow = tuple[str, str | None, str]
EntityRow = tuple[str, str, str]
ChunkRow = tuple[str, str, str]
LexemeOccurrenceRow = tuple[str, str]


class AnnotationValidationError(ValueError):
    """Raised when annotation rows fail structural validation before persistence."""

    def __init__(self, failures: Sequence[str]) -> None:
        """Build a validation error from one or more failure messages."""
        self.failures = tuple(failures)
        super().__init__("; ".join(self.failures))


def _failures_or_summary(failures: list[str]) -> dict[str, str | int]:
    """Raise collected failures or return the persisted validation summary."""
    if failures:
        raise AnnotationValidationError(failures)
    return {"validation_status": "passed", "validation_errors": 0}


def validate_spacy_annotation_output(
    *,
    text_length: int,
    span_map: Mapping[tuple[int, int], str],
    sentence_ids: Set[str],
    lang: str,
    tokens: Sequence[TokenRow],
    feats: Sequence[FeatureRow],
    deps: Sequence[DependencyRow],
    ents: Sequence[EntityRow],
    chunks: Sequence[ChunkRow],
    coref: Mapping[str, Sequence[str]],
    lex_occ: Sequence[LexemeOccurrenceRow],
    language_resolution: AnnotationLanguageResolution,
) -> dict[str, str | int]:
    """Validate spaCy-derived rows before they become corpus facts.

    Args:
        text_length: Character length of the source document text.
        span_map: Registered span offsets to span ids for this document/run.
        sentence_ids: Sentence ids belonging to this document.
        lang: The annotation language selected for this run.
        tokens: Token rows prepared for persistence.
        feats: Morphological feature rows prepared for persistence.
        deps: Dependency rows prepared for persistence.
        ents: Entity mention rows prepared for persistence.
        chunks: Chunk rows prepared for persistence.
        coref: Coreference chain members by surface key.
        lex_occ: Lexeme occurrence projection prepared for persistence.
        language_resolution: Resolved language/model choice.

    Returns:
        A compact summary suitable for job and provenance output.

    Raises:
        AnnotationValidationError: If any structural invariant fails.
    """
    failures: list[str] = []
    _validate_language(lang, language_resolution, failures)
    span_ids = _validate_spans(text_length, span_map, failures)
    token_ids = _validate_tokens(tokens, span_ids, sentence_ids, failures)
    _validate_features(feats, token_ids, failures)
    _validate_dependencies(deps, token_ids, failures)
    _validate_span_rows("entity", ents, span_ids, failures)
    _validate_span_rows("chunk", chunks, span_ids, failures)
    _validate_coref(coref, span_ids, failures)
    _validate_lexeme_projection(tokens, lex_occ, failures)
    return _failures_or_summary(failures)


def _validate_language(
    lang: str,
    language_resolution: AnnotationLanguageResolution,
    failures: list[str],
) -> None:
    """Validate that the selected annotation language is registry-supported."""
    if lang != language_resolution.annotation_language:
        failures.append(
            "annotation language mismatch: "
            f"run={lang!r} resolution={language_resolution.annotation_language!r}"
        )
    expected_model = CAPABILITY_MODELS.get(language_resolution.annotation_language)
    if expected_model is None:
        failures.append(
            f"unsupported annotation language {language_resolution.annotation_language!r}"
        )
    elif language_resolution.model != expected_model:
        failures.append(
            "annotation model mismatch: "
            f"{language_resolution.annotation_language!r} expects {expected_model!r}, "
            f"got {language_resolution.model!r}"
        )
    if language_resolution.fallback_policy not in {"fail", "fallback"}:
        failures.append(
            f"unsupported language fallback policy {language_resolution.fallback_policy!r}"
        )


def _validate_spans(
    text_length: int,
    span_map: Mapping[tuple[int, int], str],
    failures: list[str],
) -> set[str]:
    """Validate registered span offsets and return the known span ids."""
    known: set[str] = set()
    for (start, end), span_id in span_map.items():
        if start < 0 or end > text_length or start >= end:
            failures.append(
                f"span {span_id!r} outside document bounds: {start}:{end} of {text_length}"
            )
        if span_id in known:
            failures.append(f"duplicate span id in annotation output: {span_id!r}")
        known.add(span_id)
    return known


def _validate_tokens(
    tokens: Sequence[TokenRow],
    span_ids: set[str],
    sentence_ids: Set[str],
    failures: list[str],
) -> set[str]:
    """Validate token ids, spans, sentence references, and ordinals."""
    token_ids: set[str] = set()
    ordinals: set[int] = set()
    previous: int | None = None
    for token_id, span_id, sentence_id, ordinal, _lexeme_uid, _upos, _xpos in tokens:
        if token_id in token_ids:
            failures.append(f"duplicate token id in annotation output: {token_id!r}")
        token_ids.add(token_id)
        if span_id not in span_ids:
            failures.append(f"unknown token span {span_id!r} for token {token_id!r}")
        if sentence_id is not None and sentence_id not in sentence_ids:
            failures.append(f"unknown token sentence {sentence_id!r} for token {token_id!r}")
        if ordinal < 0:
            failures.append(f"negative token ordinal {ordinal} for token {token_id!r}")
        if ordinal in ordinals:
            failures.append(f"duplicate token ordinal {ordinal}")
        ordinals.add(ordinal)
        if previous is not None and ordinal <= previous:
            failures.append(f"token ordinals are not strictly increasing at {token_id!r}")
        previous = ordinal
    return token_ids


def _validate_features(
    feats: Sequence[FeatureRow],
    token_ids: set[str],
    failures: list[str],
) -> None:
    """Validate token-feature rows point at this run's token ids."""
    for token_id, feature, _value in feats:
        if token_id not in token_ids:
            failures.append(f"unknown feature token {token_id!r} for feature {feature!r}")


def _validate_dependencies(
    deps: Sequence[DependencyRow],
    token_ids: set[str],
    failures: list[str],
) -> None:
    """Validate dependency rows point only at this run's tokens."""
    seen_dependents: set[str] = set()
    for dependent, head, deprel in deps:
        if dependent not in token_ids:
            failures.append(f"unknown dependency dependent {dependent!r}")
        if dependent in seen_dependents:
            failures.append(f"duplicate dependency row for token {dependent!r}")
        seen_dependents.add(dependent)
        if head is not None and head not in token_ids:
            failures.append(f"unknown dependency head {head!r} for token {dependent!r}")
        if head is None and deprel != "root":
            failures.append(f"root dependency for token {dependent!r} must use deprel='root'")
        if head is not None and deprel == "root":
            failures.append(f"non-root dependency for token {dependent!r} uses deprel='root'")


def _validate_span_rows(
    row_name: str,
    rows: Sequence[EntityRow] | Sequence[ChunkRow],
    span_ids: set[str],
    failures: list[str],
) -> None:
    """Validate rows whose second tuple field is a span id."""
    for row_id, span_id, _label in rows:
        if span_id not in span_ids:
            failures.append(f"unknown {row_name} span {span_id!r} for row {row_id!r}")


def _validate_coref(
    coref: Mapping[str, Sequence[str]],
    span_ids: set[str],
    failures: list[str],
) -> None:
    """Validate coreference chains point only at this run's span ids."""
    for surface, chain_spans in coref.items():
        for span_id in chain_spans:
            if span_id not in span_ids:
                failures.append(f"unknown coref span {span_id!r} for chain {surface!r}")


def _validate_lexeme_projection(
    tokens: Sequence[TokenRow],
    lex_occ: Sequence[LexemeOccurrenceRow],
    failures: list[str],
) -> None:
    """Validate ``lexeme_occurrences`` is exactly the token lexeme projection."""
    expected = [
        (span_id, lexeme_uid) for _, span_id, _, _, lexeme_uid, _, _ in tokens if lexeme_uid
    ]
    if list(lex_occ) != expected:
        failures.append("lexeme projection does not match token lexeme links")
