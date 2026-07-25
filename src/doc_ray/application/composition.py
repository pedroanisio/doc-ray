"""Pure citation and response-budget primitives for evidence composition."""

from __future__ import annotations

from typing import Any


def budget_passages(
    passages: list[dict[str, Any]], budget_chars: int
) -> tuple[list[dict[str, Any]], int]:
    """Keep leading passages within *budget_chars*, reporting the dropped count."""
    kept: list[dict[str, Any]] = []
    total = 0
    for index, passage in enumerate(passages):
        size = len(str(passage.get("text") or ""))
        if kept and total + size > budget_chars:
            return kept, len(passages) - index
        kept.append(passage)
        total += size
    return kept, 0


def budget_gap(dropped: int, noun: str, hint: str) -> str:
    """Describe a response-budget drop so a size cap is never silent."""
    return f"Response budget reached: dropped {dropped} lower-ranked {noun}; {hint}."


def cite(document_id: str, title: str | None = None, ordinal: int | None = None) -> dict[str, Any]:
    """Build a citation handle that resolves to a document or exact sentence."""
    label = title or f"document {document_id}"
    handle: dict[str, Any] = {"document_id": document_id}
    if ordinal is not None:
        handle["sentence_ordinal"] = ordinal
        handle["ref"] = f"{label} · sentence {ordinal}"
    else:
        handle["ref"] = label
    return handle


def clip_text(text: str | None, cap: int) -> str | None:
    """Clip *text* to *cap* characters with an exact, visible omission marker."""
    if text is None or len(text) <= cap:
        return text
    return f"{text[:cap]}… [+{len(text) - cap} chars]"
