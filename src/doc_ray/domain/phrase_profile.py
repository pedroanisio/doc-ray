"""Versioned surface-word phrase matching with offset-exact occurrences.

``surface_word_v1`` is the shared definition of a word, phrase adjacency, and
occurrence for retrieval. It is pure standard-library domain logic: callers can
use it from adapters, APIs, or offline evaluation without importing a database or
an NLP model.

Profile identifiers are durable data contracts. Matches and counts must always
carry the identifier of the profile that produced them. Changing any policy knob
requires a new identifier and a new profile instance; ``surface_word_v1`` is
never changed in place. A future lemma profile should reuse these functions with
another instance rather than fork the algorithm.

Offsets always index the original input. Typographic folding is imported from
``search_terms`` and is length-preserving. If Unicode case folding changes a
word's length (for example ``İ``), normalization falls back to the original
case-sensitive spelling rather than risk an incorrect citation span.
"""

from __future__ import annotations

from dataclasses import dataclass

from doc_ray.domain.search_terms import CONFUSABLE_CLASSES, fold

__all__ = [
    "CONFUSABLE_CLASSES",
    "PROFILE_SURFACE_WORD_V1",
    "PhraseProfile",
    "SURFACE_WORD_V1",
    "find_occurrences",
    "fold",
    "phrase_words",
    "word_spans",
]

Span = tuple[int, int]
PROFILE_SURFACE_WORD_V1 = "surface_word_v1"


@dataclass(frozen=True, slots=True)
class PhraseProfile:
    """Immutable policy for surface-word segmentation and phrase matching.

    Attributes:
        identifier: Durable label attached to matches and counts.
        include_digits: Whether Unicode digits are word characters.
        include_underscore: Whether underscore is a word character.
        joiners: Non-word characters allowed between adjacent phrase words.
        count_overlaps: Whether a match may begin inside the preceding match.
    """

    identifier: str
    include_digits: bool
    include_underscore: bool
    joiners: frozenset[str]
    count_overlaps: bool


_JOINER_CANONICALS = frozenset({"'", "-"})
_SURFACE_WORD_JOINERS = frozenset(
    member
    for confusable_class in CONFUSABLE_CLASSES
    if confusable_class[0] in _JOINER_CANONICALS
    for member in confusable_class
)

SURFACE_WORD_V1 = PhraseProfile(
    identifier=PROFILE_SURFACE_WORD_V1,
    # OD-1: BL-164 terms such as ``2024`` must remain addressable words.
    include_digits=True,
    # OD-1: ``shared_buffers`` is one surface word rather than two.
    include_underscore=True,
    # OD-2: extracted hyphen/apostrophe variants preserve word adjacency.
    joiners=_SURFACE_WORD_JOINERS,
    # OD-3: positional engines emit every start, so stage 1 must count them too.
    count_overlaps=True,
)


def _is_word_character(char: str, profile: PhraseProfile) -> bool:
    """Return whether one Unicode character belongs to a profile word."""
    # U+02BC MODIFIER LETTER APOSTROPHE is alphabetic in Unicode but OD-2 names
    # it as a joiner, so explicit profile structure takes precedence.
    if char in profile.joiners:
        return False
    return (
        char.isalpha()
        or (profile.include_digits and char.isdigit())
        or (profile.include_underscore and char == "_")
    )


def word_spans(text: str, *, profile: PhraseProfile = SURFACE_WORD_V1) -> tuple[Span, ...]:
    """Return ordered, non-overlapping word spans into the original *text*.

    Unicode letters are always word characters. The profile decides whether
    digits and underscore join them. Every other character is a separator, so
    hyphenated forms have multiple word spans even when the hyphen is an allowed
    phrase joiner.

    Args:
        text: Source text whose offsets must be preserved.
        profile: Versioned segmentation policy.

    Returns:
        ``(start, end)`` pairs using Python's half-open string offsets.
    """
    spans: list[Span] = []
    start: int | None = None
    for index, char in enumerate(text):
        if _is_word_character(char, profile):
            if start is None:
                start = index
        elif start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(text)))
    return tuple(spans)


def _normalized_word(word: str) -> str:
    """Fold and case-fold *word*, falling back to exact case if length changes."""
    normalized = fold(word).casefold()
    return normalized if len(normalized) == len(word) else word


def _words_at(text: str, spans: tuple[Span, ...]) -> tuple[str, ...]:
    """Normalize the words selected by *spans* without changing their offsets."""
    return tuple(_normalized_word(text[start:end]) for start, end in spans)


def phrase_words(phrase: str, *, profile: PhraseProfile = SURFACE_WORD_V1) -> tuple[str, ...]:
    """Return the normalized word tuple for *phrase*.

    Empty and punctuation-only phrases return ``()``. A single word is a valid
    phrase. This function reports segmentation only; :func:`find_occurrences`
    additionally rejects a query whose internal separators violate the profile's
    adjacency policy.

    Args:
        phrase: Caller-supplied phrase.
        profile: Versioned segmentation and normalization policy.

    Returns:
        Normalized surface words in source order.
    """
    spans = word_spans(phrase, profile=profile)
    return _words_at(phrase, spans)


def _separator_is_adjacent(
    text: str, left_end: int, right_start: int, profile: PhraseProfile
) -> bool:
    """Return whether one inter-word separator preserves phrase adjacency."""
    return all(char.isspace() or char in profile.joiners for char in text[left_end:right_start])


def _all_words_are_adjacent(text: str, spans: tuple[Span, ...], profile: PhraseProfile) -> bool:
    """Return whether every neighboring word in *spans* forms one phrase."""
    return all(
        _separator_is_adjacent(text, left[1], right[0], profile)
        for left, right in zip(spans, spans[1:], strict=False)
    )


def _prefix_table(pattern: tuple[str, ...]) -> tuple[int, ...]:
    """Build the KMP failure table used for linear, overlap-aware matching."""
    table = [0] * len(pattern)
    matched = 0
    for index in range(1, len(pattern)):
        while matched and pattern[index] != pattern[matched]:
            matched = table[matched - 1]
        if pattern[index] == pattern[matched]:
            matched += 1
            table[index] = matched
    return tuple(table)


def find_occurrences(
    text: str,
    phrase: str,
    *,
    profile: PhraseProfile = SURFACE_WORD_V1,
) -> tuple[Span, ...]:
    """Return every profile-valid occurrence span of *phrase* in *text*.

    Matching is case-insensitive when normalization is length-safe, treats the
    profile's joiners like whitespace for adjacency, and resets at all other
    punctuation. The returned span begins at the first matched word and ends at
    the last, preserving the source's spelling and separators. KMP keeps the
    search O(words in text + words in phrase + matches) and naturally supports
    overlapping starts.

    Args:
        text: Source text searched without copying or rewriting offsets.
        phrase: Query phrase under the same profile.
        profile: Versioned matching policy.

    Returns:
        Ordered half-open spans into *text*. Invalid or empty phrases return
        ``()`` rather than matching every boundary.
    """
    query_spans = word_spans(phrase, profile=profile)
    if not query_spans or not _all_words_are_adjacent(phrase, query_spans, profile):
        return ()
    pattern = _words_at(phrase, query_spans)

    source_spans = word_spans(text, profile=profile)
    if len(source_spans) < len(pattern):
        return ()
    source_words = _words_at(text, source_spans)
    prefix = _prefix_table(pattern)

    occurrences: list[Span] = []
    matched = 0
    previous_end: int | None = None
    for index, ((start, end), word) in enumerate(zip(source_spans, source_words, strict=True)):
        if previous_end is not None and not _separator_is_adjacent(
            text, previous_end, start, profile
        ):
            matched = 0

        while matched and word != pattern[matched]:
            matched = prefix[matched - 1]
        if word == pattern[matched]:
            matched += 1

        if matched == len(pattern):
            occurrence_start = source_spans[index - len(pattern) + 1][0]
            occurrences.append((occurrence_start, end))
            matched = prefix[matched - 1] if profile.count_overlaps else 0
        previous_end = end

    return tuple(occurrences)
