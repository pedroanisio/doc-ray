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


def _search_coverage(hits: list[dict[str, Any]], limit: int) -> tuple[dict[str, Any], str | None]:
    """Summarise where *hits* fall in the text, and steer when the cap was reached.

    The failure mode this addresses (BL-145): a high-frequency term floods one
    region and starves the rest, so a sparse-looking result reads as *missing
    content* when it is really a *retrieval* problem. Reporting the ordinal span
    and flagging a maxed-out result turns that into a legible query diagnosis the
    agent can act on — narrow the term, or read the untouched ordinal ranges.
    """
    ordinals = [h["ordinal"] for h in hits if isinstance(h.get("ordinal"), int)]
    limit_reached = len(hits) >= limit
    coverage: dict[str, Any] = {
        "hit_count": len(hits),
        "ordinal_min": min(ordinals) if ordinals else None,
        "ordinal_max": max(ordinals) if ordinals else None,
        "limit": limit,
        "limit_reached": limit_reached,
    }
    if not limit_reached:
        return coverage, None
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
