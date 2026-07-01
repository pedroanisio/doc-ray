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

from .ulid import ULIDFactory

_TERMINATORS = ".!?…"
_CLOSERS = ")]}\"'”’»"
_OPENERS = "([{\"'“‘«¿¡"
_WS = " \t\r\n\f\v"

# Lowercased abbreviations (trailing dot stripped) that do NOT end a sentence.
_ABBREV = {
    # English titles / common
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "eg",
    "ie", "no", "fig", "al", "inc", "ltd", "co", "corp", "dept", "univ",
    "gen", "sen", "gov", "rep", "cf", "pp", "vol", "ch", "sec", "art",
    "approx", "min", "max", "est", "cap", "ed", "eds", "esp", "ca",
    # months
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
    # Portuguese
    "sra", "dra", "av", "pag", "cap", "ilmo", "exmo", "prof", "eng",
}

# Acronym / initial pattern like "U.S", "e.g", "a.m", "A".
_ACRONYM = re.compile(r"([a-z]\.){1,}[a-z]?$")


@dataclass(frozen=True)
class Sentence:
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
    """True when the terminator at `i` is an abbreviation/initial/acronym dot
    rather than a real sentence end. Only '.' is ambiguous this way."""
    if text[i] != ".":
        return False
    token = _trailing_token(text, i)
    if not token:
        return False
    key = token.lower().strip(".")
    if key in _ABBREV:
        return True
    if len(token) == 1 and token.isalpha():   # single initial, e.g. "A."
        return True
    if _ACRONYM.fullmatch(token.lower()):      # "U.S", "e.g", "a.m"
        return True
    return False


def _emit(out: list[Sentence], text: str, start: int, end: int) -> None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    lead = len(raw) - len(raw.lstrip())
    cs = start + lead
    ce = cs + len(stripped)
    out.append(Sentence(" ".join(stripped.split()), cs, ce))


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
                starts_new = (
                    at_eof or nxt.isupper() or nxt.isdigit() or nxt in _OPENERS
                )
                if starts_new and not _is_false_boundary(text, i):
                    _emit(sents, text, seg_start, j)
                    seg_start = k
                    i = k
                    continue
        i += 1
    _emit(sents, text, seg_start, n)
    return sents


def build_sentences(full_text: str, factory: ULIDFactory | None = None) -> list[dict]:
    """Split `full_text` and attach a monotonic ULID + reading-order ordinal
    to each sentence. Returns row-dicts ready for insertion.

    A fresh monotonic factory per document keeps ULIDs strictly increasing in
    lockstep with `ordinal`.
    """
    factory = factory or ULIDFactory()
    rows: list[dict] = []
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
