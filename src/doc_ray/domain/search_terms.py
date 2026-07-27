"""The literal-substring matching rule for sentence search, stated once.

``search_sentences`` (GraphQL/MCP) and the webapp's keyword search both promise
the *same* thing — a case-insensitive **literal substring** match, no stemming,
no lemmatization (``doc_ray.domain.term_frequency`` documents that contract, and
``shaping._unflooded_note`` states it to the caller on a zero-hit result). Each
surface built its own ``ILIKE '%' || term || '%'`` pattern and split its own KWIC
window with :meth:`str.partition`, and neither kept the promise:

* **``%`` and ``_`` reached Postgres as wildcards.** ``shared_buffers`` also
  matched ``sharedXbuffers``; ``100%`` matched every sentence containing ``100``.
  That is the opposite of literal, and it lands on exactly the term class BL-164
  redirects here ("the lexeme index cannot hold ``shared_buffers`` — use
  ``search_sentences``, whose literal-substring match handles it").
* **The ASCII apostrophe never matched the typographic one.** PDF/EPUB
  extraction preserves ``’``, ``“…”`` and en/em dashes, so a term typed with
  ``'`` returned a confident, *exhaustive*-flagged zero for a phrase the document
  uses throughout — an absence claim that was false.
* **The KWIC split was case-sensitive while the match was case-insensitive.** A
  hit ILIKE found could fail ``partition``, returning an empty ``hit`` with the
  whole sentence dumped into ``pre``.

Two rules keep the fix honest. The fold is **1:1 and length-preserving** — every
confusable maps to exactly one ASCII character — so an offset into folded text is
an offset into the original and the citation handles (``charStart``/``charEnd``)
stay exact. And matching is **widened, never narrowed**: folding only makes a
typed term match more spellings of the same string, so no result that matched
before stops matching now.

Pure standard library, no database (the ``domain`` split of BL-146):
:mod:`doc_ray.adapters.sentence_search_sql` turns a :class:`MatchPlan` into SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

#: Characters that a reader means as one character but a corpus stores as
#: several. Members of a class are interchangeable for matching; the FIRST member
#: is the canonical (ASCII) form every other member folds to. Deliberately
#: narrow: only marks that document extraction actually emits in place of an
#: ASCII original, because each extra member multiplies the pattern count.
CONFUSABLE_CLASSES: tuple[tuple[str, ...], ...] = (
    ("'", "’", "‘", "ʼ"),  # ' ' ' ʼ  — apostrophe / single quote
    ("-", "–", "—", "‐"),  # - – — ‐  — hyphen / en dash / em dash
    ('"', "“", "”"),  # " " "     — double quote
)

#: Fold table: every non-canonical member -> its class's canonical character.
#: One character in, one character out, so :func:`fold` never changes a length.
_FOLD: dict[int, str] = {ord(member): cls[0] for cls in CONFUSABLE_CLASSES for member in cls[1:]}

#: The same table as the two positional strings Postgres ``translate()`` takes,
#: for the (rare) fallback plan that folds the column instead of the pattern.
FOLD_SOURCE: str = "".join(member for cls in CONFUSABLE_CLASSES for member in cls[1:])
FOLD_TARGET: str = "".join(cls[0] for cls in CONFUSABLE_CLASSES for _ in cls[1:])

#: Which class (if any) a character belongs to, for pattern expansion.
_CLASS_OF: dict[str, tuple[str, ...]] = {
    member: cls for cls in CONFUSABLE_CLASSES for member in cls
}

#: Ceiling on the OR-ed patterns one term may expand into. Each pattern is a
#: separate trigram-index probe, so the expansion has to stay bounded; a term
#: with three or more confusable characters folds the column instead (slower,
#: index-degraded, still correct — see :class:`MatchPlan`).
MAX_PATTERNS = 16

#: ``LIKE`` metacharacters, escaped so a literal term stays literal. The escape
#: character is the Postgres default (backslash), stated explicitly in the SQL.
_LIKE_ESCAPE = {"\\": "\\\\", "%": "\\%", "_": "\\_"}


def fold(text: str) -> str:
    """Return *text* with every typographic confusable folded to its ASCII form.

    Length-preserving by construction: each mapping is one character to one
    character, so ``len(fold(t)) == len(t)`` and offsets survive the fold.
    """
    return text.translate(_FOLD)


@dataclass(frozen=True)
class MatchPlan:
    """How to match one literal term, decided without touching the database.

    Attributes:
        patterns: ``ILIKE`` patterns (already ``%``-wrapped and escaped). Any
            match counts — the SQL ORs them. Always at least one.
        fold_column: When True the column must be folded (Postgres
            ``translate()``) before comparison, because expanding the term would
            have exceeded :data:`MAX_PATTERNS`. Correct but index-degraded, so it
            is the exception, not the rule.
    """

    patterns: tuple[str, ...]
    fold_column: bool


def _escape(char: str) -> str:
    """Escape one character for use inside a ``LIKE`` pattern."""
    return _LIKE_ESCAPE.get(char, char)


def _variant_count(term: str) -> int:
    """How many patterns *term* would expand into (product of its class sizes)."""
    count = 1
    for char in term:
        count *= len(_CLASS_OF.get(char, (char,)))
        if count > MAX_PATTERNS:  # short-circuit: a long term can overflow fast
            return count
    return count


def match_plan(term: str) -> MatchPlan:
    """Return the bounded matching plan for the literal *term*.

    A term with no confusable characters (the overwhelming majority) yields
    exactly one pattern, so the generated SQL is shape-identical to the single
    ``ILIKE`` this replaced — same trigram index, same cost.

    Args:
        term: The caller's search term, taken literally (``%`` and ``_`` are
            data, not wildcards).

    Returns:
        The :class:`MatchPlan` to hand to the SQL builder.
    """
    if _variant_count(term) > MAX_PATTERNS:
        # Too many spellings to enumerate; fold the column instead and match the
        # single folded pattern. Slower, never wrong.
        return MatchPlan(patterns=(_pattern(fold(term)),), fold_column=True)
    options = [tuple(_CLASS_OF.get(char, (char,))) for char in term]
    spellings = ["".join(combo) for combo in product(*options)] if options else [""]
    return MatchPlan(patterns=tuple(_pattern(s) for s in spellings), fold_column=False)


def _pattern(spelling: str) -> str:
    """Wrap one exact spelling into an escaped ``%…%`` containment pattern."""
    return "%" + "".join(_escape(char) for char in spelling) + "%"


def _comparable(text: str) -> str | None:
    """Return *text* folded and lowercased, or ``None`` if that changed its length.

    A handful of characters (``İ`` is the canonical example) lowercase to more
    than one character, which would slide every offset after them. Rather than
    return a wrong span, the caller falls back to an exact split.
    """
    lowered = fold(text).lower()
    return lowered if len(lowered) == len(text) else None


def kwic(text: str, term: str) -> tuple[str, str, str]:
    """Split *text* around its first match of *term* into ``(pre, hit, post)``.

    Matches the way the database matched — case-insensitively and across
    typographic confusables — so the highlighted window is the text the row was
    returned for. ``hit`` is the substring **as it appears in the document**
    (``Hick’s``), not as the caller typed it (``Hick's``); quoting the caller's
    spelling back would misquote the source.

    Returns ``(text, "", "")`` when *term* does not occur, which is what a caller
    renders when the match came from a different part of the row.
    """
    if not term:
        return (text, "", "")
    haystack = _comparable(text)
    needle = _comparable(term)
    if haystack is None or needle is None:  # length-changing case fold; be exact
        pre, sep, post = text.partition(term)
        return (pre, term if sep else "", post)
    index = haystack.find(needle)
    if index < 0:
        return (text, "", "")
    end = index + len(needle)
    return (text[:index], text[index:end], text[end:])
