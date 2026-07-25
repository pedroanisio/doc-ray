"""Shared citation and response-budget primitives for the composition tools.

Split out of :mod:`doc_ray.entrypoints.mcp.compose` (D-015). These three helpers
are pure and dependency-free, and every composition tool (`briefing_pack`,
`entity_dossier`, `document_evidence`) uses them: `_cite` mints the traceable
citation handle that makes "source before synthesis" real, and `_budget_passages`
/ `_budget_gap` bound a response and report — never silently drop — what did not
fit. Kept in one place so the three tools cannot drift on how they cite or cap.
"""

from __future__ import annotations

from typing import Any


def _budget_passages(
    passages: list[dict[str, Any]], budget_chars: int
) -> tuple[list[dict[str, Any]], int]:
    """Keep the leading passages whose cumulative text fits *budget_chars*.

    At least one passage always survives, and order is preserved so the
    highest-ranked passages win. Returns ``(kept, dropped_count)``.
    """
    kept: list[dict[str, Any]] = []
    total = 0
    for index, passage in enumerate(passages):
        size = len(str(passage.get("text") or ""))
        if kept and total + size > budget_chars:
            return kept, len(passages) - index
        kept.append(passage)
        total += size
    return kept, 0


def _budget_gap(dropped: int, noun: str, hint: str) -> str:
    """A gap line reporting a response-budget drop — caps are never silent."""
    return f"Response budget reached: dropped {dropped} lower-ranked {noun}; {hint}."


def _cite(document_id: str, title: str | None = None, ordinal: int | None = None) -> dict[str, Any]:
    """Build a citation handle an agent can quote and later re-verify."""
    label = title or f"document {document_id}"
    cite: dict[str, Any] = {"document_id": document_id}
    if ordinal is not None:
        cite["sentence_ordinal"] = ordinal
        cite["ref"] = f"{label} · sentence {ordinal}"
    else:
        cite["ref"] = label
    return cite
