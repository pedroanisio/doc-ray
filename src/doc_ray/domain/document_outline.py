"""Build a navigable map of a document, in the ordinals the readers speak (BL-146).

``read_document_sentences(from_ordinal=…, to_ordinal=…)`` gives random access to
any slice, but it is only as useful as the agent's ability to guess an ordinal.
This module turns a document into a small list of addressable ranges.

**Where the structure comes from, and how far it can be trusted.** Every
extractor normalizes the headings it detects into ATX markers (``#``…``######``)
inside the stored text: markup formats carry them natively, and PDFs get them
from the relative-font-size heuristic in :mod:`doc_ray.domain.pdf_layout`. That
heuristic is uneven by nature — measured across the live corpus it finds 723
headings in a technical book, 17 in *Dune*, and **none at all** in *Dune
Messiah*. A tool that answered ``[]`` for a novel would fail precisely the
document the feature exists for, so :func:`plan_uniform_buckets` provides an even
split as a fallback. The two are never conflated: the caller reports which one it
used, and a grid is described as a grid rather than dressed up as a table of
contents.

Pure and deterministic — the adapter supplies the text, the ordinals, and the
page numbers; nothing here touches a database.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

#: An ATX heading line: 1–6 hashes, whitespace, then a non-empty title. Anchored
#: per line, so ``#hashtag`` (no space) and a seven-hash run are not headings.
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(\S.*?)[ \t\r]*$", re.MULTILINE)

#: A fenced code-block delimiter: three or more backticks or tildes, with an
#: optional info string. Per CommonMark a fence is closed only by the *same*
#: character, at least as long, and carrying no info string of its own — so
#: ```` ```python ```` inside an open block is content, not a close.
FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>\S.*?)?[ \t\r]*$")

#: ``(level, title, char_start, ordinal, page)`` — a heading resolved onto the
#: sentence that contains it. ``page`` is ``None`` for documents without layout.
ResolvedHeading = tuple[int, str, int, int, "int | None"]


@dataclass(frozen=True)
class OutlineEntry:
    """One addressable region of a document.

    ``ordinal_start``/``ordinal_end`` are inclusive and feed straight into
    ``read_document_sentences``; ``char_start``/``char_end`` are the absolute
    offsets into the document text, and ``page_start``/``page_end`` (when layout
    exists) feed the ``page`` reader instead.
    """

    level: int
    title: str
    ordinal_start: int
    ordinal_end: int
    char_start: int
    char_end: int
    page_start: int | None
    page_end: int | None


def parse_heading_lines(text: str | None) -> list[tuple[int, str, int]]:
    """Extract ``(level, title, char_start)`` for every ATX heading in *text*.

    ``char_start`` is the offset of the first ``#``, i.e. the same absolute
    coordinate system the ``spans`` table stores, so a heading can be joined to
    the sentence that contains it without a second pass over the text.
    """
    if not text:
        return []
    return [
        (len(match.group(1)), match.group(2).strip(), match.start())
        for match in HEADING_RE.finditer(text)
    ]


def headings_outside_fences(
    lines: Iterable[tuple[str, int]],
) -> list[tuple[int, str, int]]:
    """Keep only the ATX headings that are *not* inside a fenced code block.

    A ``#`` at the start of a line is a Python or shell comment as often as it is
    a heading, and code reaches the stored text from two directions: EPUB/HTML
    ``<pre>`` blocks, and ``.md`` documents stored verbatim. Detecting headings
    one line at a time cannot tell the two apart, and the failure is not
    cosmetic — measured on *Agentic Architectural Patterns* it produced 1,278
    "headings" across 4,897 sentences, with comment lines such as
    ``# --- Initialize the LLM ---`` presented as chapter titles. An outline that
    misdescribes a document that badly is worse than no outline, because callers
    trust ``source="headings"`` as real structure.

    *lines* is every heading-or-fence candidate line in document order, as
    ``(line, char_start)``. Non-candidate lines are irrelevant: only a fence
    delimiter can change fence state, so the caller may filter them out in SQL
    without affecting the result.

    Returns ``(level, title, char_start)`` for the surviving headings.
    """
    open_fence: str | None = None
    kept: list[tuple[int, str, int]] = []
    for line, char_start in lines:
        fence = FENCE_RE.match(line)
        if fence is not None:
            marker = fence.group("fence")
            if open_fence is None:
                open_fence = marker
            elif (
                marker[0] == open_fence[0]
                and len(marker) >= len(open_fence)
                and fence.group("info") is None
            ):
                open_fence = None
            continue
        if open_fence is not None:
            continue
        parsed = parse_heading_lines(line)
        if parsed:
            level, title, _ = parsed[0]
            kept.append((level, title, char_start))
    return kept


def build_heading_outline(
    headings: Iterable[ResolvedHeading],
    total_sentences: int,
    total_chars: int | None = None,
) -> list[OutlineEntry]:
    """Turn resolved headings into contiguous, gapless, non-overlapping ranges.

    Each heading owns the text from where it starts until the next heading
    begins; the last runs to the final sentence. Two refinements keep the map
    honest rather than merely tidy:

    * Several headings resolving to the same sentence collapse into one entry.
      A PDF title page really does extract as ``# CHAPTERHOUSE: # DUNE ### BOOK
      SIX`` inside a single sentence, and three entries with identical ranges
      would be noise. The outermost (lowest) level wins.
    * The first entry is extended back to ordinal 0, so no prefix of the
      document is unreachable through the map.

    Args:
        headings: ``(level, title, char_start, ordinal, page)`` tuples.
        total_sentences: The document's sentence count; ``0`` yields no entries.
        total_chars: Document length, used to close the final entry's character
            range. Omitted, the last entry's ``char_end`` equals its start.

    Returns:
        Entries in reading order, covering ``0 .. total_sentences - 1`` exactly.
    """
    if total_sentences <= 0:
        return []

    # One heading per sentence; sorting by level first makes the outermost win.
    unique: dict[int, ResolvedHeading] = {}
    for heading in sorted(headings, key=lambda h: (h[3], h[0], h[2])):
        unique.setdefault(heading[3], heading)
    ordered = sorted(unique.values(), key=lambda h: h[3])
    if not ordered:
        return []

    entries: list[OutlineEntry] = []
    last_ordinal = total_sentences - 1
    for index, (level, title, char_start, ordinal, page) in enumerate(ordered):
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        start = 0 if index == 0 else min(ordinal, last_ordinal)
        end = min(following[3] - 1, last_ordinal) if following else last_ordinal
        if end < start:  # a later heading resolved to an earlier sentence
            continue
        entries.append(
            OutlineEntry(
                level=level,
                title=title,
                ordinal_start=start,
                ordinal_end=end,
                char_start=char_start,
                char_end=(
                    following[2] - 1
                    if following
                    else (total_chars - 1 if total_chars else char_start)
                ),
                page_start=page,
                page_end=following[4] if following else None,
            )
        )
    return entries


def select_headings(
    headings: list[tuple[int, str, int]], max_entries: int
) -> tuple[list[tuple[int, str, int]], int]:
    """Choose at most *max_entries* headings that still map the whole document.

    Two failure modes to avoid, both observed on the live corpus:

    * **Truncating in document order** would map the preface of a 723-heading
      technical book and present it as the book.
    * **Falling back to the shallowest level** is worse still where that level is
      degenerate: *Princess of Dune* carries one level-1 heading (its title) and
      69 level-2 chapters, so "keep level 1" answers with a single entry spanning
      all 10,063 sentences — an outline that maps nothing.

    So: prefer the deepest whole level that fits; if that level is too sparse to
    be a map, take the next level down and sample it **evenly**, which keeps real
    titles and still reaches the end of the document. Returns the kept headings
    (in document order) and how many were dropped, so the caller can say so
    rather than truncate silently.
    """
    if not headings or max_entries <= 0:
        return ([], 0)
    if len(headings) <= max_entries:
        return (headings, 0)

    levels = sorted({heading[0] for heading in headings})
    by_level = {level: [h for h in headings if h[0] <= level] for level in levels}
    fitting = [level for level in levels if len(by_level[level]) <= max_entries]
    if fitting:
        best = by_level[fitting[-1]]
        deeper = [level for level in levels if len(by_level[level]) > max_entries]
        # A level that fits but barely populates the budget is not a map — one
        # entry for a 10,000-sentence novel maps nothing. Sample the next level
        # down instead, which spends the budget on real titles.
        if len(best) * 2 >= max_entries or not deeper:
            return (best, len(headings) - len(best))
        candidate = by_level[deeper[0]]
    else:
        candidate = by_level[levels[0]]

    step = len(candidate) / max_entries
    sampled = [
        candidate[min(int(index * step), len(candidate) - 1)] for index in range(max_entries)
    ]
    unique = list(dict.fromkeys(sampled))
    return (unique, len(headings) - len(unique))


def plan_uniform_buckets(total_sentences: int, max_entries: int) -> list[tuple[int, int]]:
    """Split a document into at most *max_entries* even, inclusive ordinal ranges.

    The fallback for documents where heading detection found nothing — a
    navigation grid, not a table of contents, and labelled as such by the caller.
    Sizes differ by at most one sentence, and the ranges cover every sentence
    exactly once. A document shorter than *max_entries* yields one bucket per
    sentence rather than empty ones.
    """
    if total_sentences <= 0 or max_entries <= 0:
        return []
    count = min(max_entries, total_sentences)
    size, remainder = divmod(total_sentences, count)
    buckets: list[tuple[int, int]] = []
    start = 0
    for index in range(count):
        length = size + (1 if index < remainder else 0)
        buckets.append((start, start + length - 1))
        start += length
    return buckets
