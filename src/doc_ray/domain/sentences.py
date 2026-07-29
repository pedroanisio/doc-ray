"""Sentence segmentation with character offsets and ULID assignment.

Splits extracted document text into sentence units, each of which becomes a
stored row keyed by a ULID. The splitter is a pragmatic, abbreviation-aware
heuristic (no heavy NLP dependency); it is intentionally modular so a stronger
segmenter (spaCy, pysbd) can replace `split_sentences` without touching the
storage layer.

Offset contract: for every returned sentence,
    full_text[char_start:char_end].split() == sentence.text.split()
i.e. offsets point at the exact raw span; `text` is the same span with
internal whitespace collapsed to single spaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from doc_ray.domain.ulid import ULIDFactory

_TERMINATORS = ".!?…"
_CLOSERS = ")]}\"'”’»"
_OPENERS = "([{\"'“‘«¿¡"
_WS = " \t\r\n\f\v"

#: Hard ceiling on one emitted unit's length, in characters.
#:
#: Not every run of text contains a sentence terminator. A book index, a table
#: of contents, or a dumped table is comma- and newline-separated prose-free
#: text, so the scan below finds no boundary and emits the whole run as ONE
#: "sentence": measured 2026-07-29, one document produced a single 30,321-char
#: row holding its entire index. That row is not merely ugly — it is silently
#: lossy downstream. Layer 20 embeds one vector per sentence with
#: all-mpnet-base-v2, whose window is 384 wordpiece tokens (roughly 1,500
#: characters of English prose, and an approximation — tokenization is
#: content-dependent); everything past it is truncated away, so ~29,000
#: characters of that row were unrepresented in semantic search while still
#: appearing to have an embedding.
#:
#: The cap is therefore set at the encoder's window rather than at a round
#: number: a unit that fits is embedded in full, and a run that does not
#: contain a sentence boundary is split at whitespace instead of being stored
#: whole. Genuine long sentences are rare above this length (2 of 8,680 in the
#: measured document), and splitting one is strictly better than embedding only
#: its first third.
MAX_SENTENCE_CHARS = 1500

# Lowercased abbreviations (trailing dot stripped) that do NOT end a sentence.
_ABBREV = {
    # English titles / common
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "st",
    "vs",
    "etc",
    "eg",
    "ie",
    "no",
    "fig",
    "al",
    "inc",
    "ltd",
    "co",
    "corp",
    "dept",
    "univ",
    "gen",
    "sen",
    "gov",
    "rep",
    "cf",
    "pp",
    "vol",
    "ch",
    "sec",
    "art",
    "approx",
    "min",
    "max",
    "est",
    "cap",
    "ed",
    "eds",
    "esp",
    "ca",
    # months
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
    # Portuguese
    "sra",
    "dra",
    "av",
    "pag",
    "ilmo",
    "exmo",
    "eng",
}

# Acronym / initial pattern like "U.S", "e.g", "a.m", "A".
_ACRONYM = re.compile(r"([a-z]\.){1,}[a-z]?$")


@dataclass(frozen=True)
class Sentence:
    """A single sentence span with character offsets into the source text.

    Attributes:
        text: The sentence text with internal whitespace collapsed to spaces.
        char_start: Start offset of the raw span in the source text.
        char_end: End offset (exclusive) of the raw span in the source text.
    """

    text: str
    char_start: int
    char_end: int


def _trailing_token(text: str, dot_index: int) -> str:
    """The run of letters/dots immediately before the '.' at dot_index."""
    j = dot_index
    while j > 0 and (text[j - 1].isalpha() or text[j - 1] == "."):
        j -= 1
    return text[j:dot_index]


def _is_false_boundary(text: str, i: int) -> bool:
    """Report whether the terminator at ``i`` is not a real sentence end.

    A dot is a false boundary when it belongs to an abbreviation, single
    initial, or acronym. Only ``.`` is ambiguous this way.
    """
    if text[i] != ".":
        return False
    token = _trailing_token(text, i)
    if not token:
        return False
    key = token.lower().strip(".")
    if key in _ABBREV:
        return True
    if len(token) == 1 and token.isalpha():  # single initial, e.g. "A."
        return True
    return bool(_ACRONYM.fullmatch(token.lower()))  # "U.S", "e.g", "a.m"


def _last_whitespace(text: str, start: int, end: int) -> int:
    """Index of the last whitespace char in ``text[start:end]``, or -1."""
    for i in range(end - 1, start - 1, -1):
        if text[i] in _WS:
            return i
    return -1


def _append(out: list[Sentence], text: str, start: int, end: int) -> None:
    """Append one unit for ``text[start:end]``, trimming surrounding whitespace."""
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    cs = start + (len(raw) - len(raw.lstrip()))
    out.append(Sentence(" ".join(stripped.split()), cs, cs + len(stripped)))


def _emit(out: list[Sentence], text: str, start: int, end: int) -> None:
    """Emit ``text[start:end]`` as one unit, or as several if it exceeds the cap.

    A segment over :data:`MAX_SENTENCE_CHARS` contained no sentence boundary for
    that many characters — an index, a contents list, a table dump — so it is
    not one sentence and must not be stored as one. It is split at the last
    whitespace before the cap (falling back to a hard cut when a single token is
    itself longer than the cap), which keeps the offset contract exact for every
    piece: each piece's offsets still address its own raw span.
    """
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    cs = start + (len(raw) - len(raw.lstrip()))
    ce = cs + len(stripped)
    if len(stripped) <= MAX_SENTENCE_CHARS:
        out.append(Sentence(" ".join(stripped.split()), cs, ce))
        return
    pos = cs
    while pos < ce:
        limit = pos + MAX_SENTENCE_CHARS
        if limit >= ce:
            _append(out, text, pos, ce)
            return
        cut = _last_whitespace(text, pos + 1, limit)
        if cut <= pos:  # one unbroken token longer than the cap
            cut = limit
        _append(out, text, pos, cut)
        pos = cut
        while pos < ce and text[pos] in _WS:  # next piece starts on content
            pos += 1


def split_sentences(text: str) -> list[Sentence]:
    """Segment `text` into sentences with character offsets."""
    sents: list[Sentence] = []
    n = len(text)
    i = 0
    seg_start = 0
    while i < n:
        if text[i] in _TERMINATORS:
            # consume a run of terminators + trailing closers  (e.g.  ?!")
            j = i + 1
            while j < n and (text[j] in _TERMINATORS or text[j] in _CLOSERS):
                j += 1
            # a real boundary must be followed by whitespace (or end of text)
            k = j
            while k < n and text[k] in _WS:
                k += 1
            had_space = k > j
            at_eof = k >= n
            if had_space or at_eof:
                nxt = text[k] if k < n else ""
                starts_new = at_eof or nxt.isupper() or nxt.isdigit() or nxt in _OPENERS
                if starts_new and not _is_false_boundary(text, i):
                    _emit(sents, text, seg_start, j)
                    seg_start = k
                    i = k
                    continue
        i += 1
    _emit(sents, text, seg_start, n)
    return sents


def build_sentences(full_text: str, factory: ULIDFactory | None = None) -> list[dict[str, object]]:
    """Split ``full_text`` into row-dicts with a ULID and reading-order ordinal.

    Each sentence becomes a row-dict ready for insertion. A fresh monotonic
    factory per document keeps ULIDs strictly increasing in lockstep with
    ``ordinal``.

    Args:
        full_text: The document text to segment; ``None`` is treated as empty.
        factory: The ULID factory to use; a fresh monotonic one is created if
            omitted.

    Returns:
        A list of row-dicts, each with ``id``, ``ordinal``, ``text``,
        ``char_start``, and ``char_end`` keys.
    """
    factory = factory or ULIDFactory()
    rows: list[dict[str, object]] = []
    for ordinal, s in enumerate(split_sentences(full_text or "")):
        rows.append(
            {
                "id": str(factory.new()),
                "ordinal": ordinal,
                "text": s.text,
                "char_start": s.char_start,
                "char_end": s.char_end,
            }
        )
    return rows
