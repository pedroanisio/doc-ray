"""Bounded, honest MCP tool responses: clipping, limit clamping, and coverage.

Split out of :mod:`doc_ray.entrypoints.mcp.corpus` (D-015). One principle,
three mechanisms: a tool response may be bounded, but the bound must be
visible — clipped KWIC windows carry an explicit ``… [+N chars]`` marker,
clamped page sizes are the limit actually forwarded upstream (never a doomed
request), and a maxed-out search declares itself in ``coverage``/``note``.
"""

from __future__ import annotations

from typing import Any

#: Per-window character cap for KWIC text fields. Upstream sentence segmentation
#: can produce mega-sentences (e.g. a whole table of contents extracted as one
#: sentence); windows are clipped with an explicit marker so tool responses stay
#: bounded and truncation is never silent.
MAX_KWIC_WINDOW_CHARS = 500

#: Hard cap the GraphQL API applies to every ``limit`` argument — a mirror of
#: :data:`doc_ray.entrypoints.api.context.MAX_PAGE_SIZE`, kept equal by a
#: contract test rather than an import (importing it would drag the API stack
#: into the MCP process). The API clamps silently; the MCP tools clamp
#: *visibly*: every tool forwards the limit it will actually be served, and
#: ``search_sentences`` reports a clamped request in ``coverage``/``note`` so
#: truncation is never mistaken for an exhaustive result (BL-162).
MAX_PAGE_SIZE = 200


def effective_limit(requested: int) -> int:
    """Clamp *requested* to ``[1, MAX_PAGE_SIZE]`` — the limit the API will serve."""
    return max(1, min(requested, MAX_PAGE_SIZE))


def clip_text(text: str | None, cap: int) -> str | None:
    """Clip *text* to *cap* characters, marking exactly how much was dropped."""
    if text is None or len(text) <= cap:
        return text
    return f"{text[:cap]}… [+{len(text) - cap} chars]"


def _clip_windows(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Clip each KWIC text window of *row* to :data:`MAX_KWIC_WINDOW_CHARS`."""
    for field in fields:
        value = row.get(field)
        if isinstance(value, str):
            row[field] = clip_text(value, MAX_KWIC_WINDOW_CHARS)
    return row


def _unflooded_note(
    hits: list[dict[str, Any]], term: str, paged: bool, document_scoped: bool
) -> str | None:
    """The note for a result that did not hit its cap — absence, or page-end, or silence.

    Only the empty cases speak. A short page of hits needs no prose: the rows are
    the answer and ``coverage.exhaustive`` already carries the completeness claim.
    """
    if hits:
        return None
    if paged:
        # Zero hits after a cursor means the previous page was the last one. The
        # earlier pages had hits, so this is a page boundary and saying anything
        # about absence here would be a fresh false negative.
        return (
            "No further hits after this cursor — the previous page was the last. "
            "This is a page boundary, not an absence claim: earlier pages were not "
            "re-scanned. Restart without `after` to scan from the beginning."
        )
    where = "this document" if document_scoped else "the corpus visible to you"
    return (
        f"0 hits. This was an exhaustive case-insensitive substring scan of {where}, "
        f"not a sample — {term!r} does not occur in it as a literal string. Absence of "
        f"the exact string is not absence of the idea: try a different spelling, "
        f"hyphenation, or spacing, or search a synonym before concluding the topic is "
        f"missing."
    )


def _search_coverage(
    hits: list[dict[str, Any]],
    limit: int,
    *,
    term: str = "",
    paged: bool = False,
    document_scoped: bool = False,
) -> tuple[dict[str, Any], str | None]:
    """Summarise where *hits* fall in the text, and steer when the cap was reached.

    The failure mode this addresses (BL-145): a high-frequency term floods one
    region and starves the rest, so a sparse-looking result reads as *missing
    content* when it is really a *retrieval* problem. Reporting the ordinal span
    and flagging a maxed-out result turns that into a legible query diagnosis the
    agent can act on — narrow the term, or read the untouched ordinal ranges.

    The mirror-image failure (BL-164): a result that was *not* truncated said
    nothing at all, so ``hit_count`` could not be told apart from "the first page
    of many", and an empty ``hits`` could not be told apart from "my query was
    wrong". Callers building grounded documents need to state absence, and silence
    is not a foundation for that claim — so ``coverage.exhaustive`` says outright
    when the scan ran to completion and ``hit_count`` IS the total. A zero-hit
    first page additionally gets that in prose, since that is the case a caller is
    most likely to misread. Paging past the end is deliberately NOT exhaustive: it
    means "no more after this cursor", never "absent".

    Args:
        hits: The rows returned for this page.
        limit: The page size actually served.
        term: The search term, echoed into the absence note.
        paged: True when an ``after`` cursor was supplied (a continuation page).
        document_scoped: True when the search was confined to one document, which
            bounds how far an absence claim reaches.

    Returns:
        The ``coverage`` mapping and the steering ``note`` (or ``None``).
    """
    ordinals = [h["ordinal"] for h in hits if isinstance(h.get("ordinal"), int)]
    limit_reached = len(hits) >= limit
    # Exhaustive means: nothing was cut off (the page did not fill) AND this page
    # is the whole result set (no earlier page preceded it). Both halves matter —
    # the second is what stops a continuation page from claiming absence.
    exhaustive = not limit_reached and not paged
    coverage: dict[str, Any] = {
        "hit_count": len(hits),
        "ordinal_min": min(ordinals) if ordinals else None,
        "ordinal_max": max(ordinals) if ordinals else None,
        "limit": limit,
        "limit_reached": limit_reached,
        "exhaustive": exhaustive,
    }
    if not limit_reached:
        return coverage, _unflooded_note(hits, term, paged, document_scoped)
    if ordinals:
        note = (
            f"Returned the maximum {limit} hits, spanning ordinals "
            f"{coverage['ordinal_min']}–{coverage['ordinal_max']}. A common term can flood one "
            f"region and hide the rest — narrow with a rarer, scene-specific term, or use "
            f"read_document_sentences by ordinal range (e.g. beyond {coverage['ordinal_max']}) "
            f"to cover the untouched text."
        )
    else:
        note = (
            f"Returned the maximum {limit} hits. A common term can flood results — narrow with "
            f"a rarer, scene-specific term, or use read_document_sentences by ordinal range."
        )
    return coverage, note
