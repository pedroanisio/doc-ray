"""Composition-grounding MCP tools over token-forwarded, RLS-scoped reads.

The tools return bounded evidence, citations, coverage, and explicit gaps. They
never author prose or write to the corpus. Briefing composition lives in the
application layer so MCP and GraphQL expose one drift-proof contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from doc_ray.application.briefing import (
    BriefingReader,
    SemanticUnavailable,
    build_briefing_pack,
    clipped_passage_text,
    ranked_passages,
    terms_tried,
)
from doc_ray.application.composition import budget_gap, budget_passages, cite
from doc_ray.entrypoints.mcp import corpus, semantic

_MAX_PASSAGE_CHARS = 1_000
_BRIEFING_PASSAGE_BUDGET = 12_000
_EVIDENCE_PASSAGE_BUDGET = 8_000
_DOSSIER_MENTION_BUDGET = 12_000
_DOSSIER_DOCUMENT_LIMIT = 50

# Compatibility aliases used by the long-standing compose regression suite.
_budget_gap = budget_gap
_budget_passages = budget_passages
_cite = cite
_terms_tried = terms_tried


class _MCPBriefingReader(BriefingReader):
    """Late-bound corpus adapter preserving per-call bearer forwarding."""

    async def full_text_search(self, topic: str, *, limit: int) -> list[dict[str, Any]]:
        return await corpus.full_text_search(topic, limit=limit)

    async def search_sentences(
        self, term: str, *, document_id: str, limit: int
    ) -> list[dict[str, Any]]:
        result = await corpus.search_sentences(term, document_id=document_id, limit=limit)
        return list(result["hits"])

    async def entity_frequency(self, *, document_id: str, limit: int) -> list[dict[str, Any]]:
        return await corpus.entity_frequency(document_id=document_id, limit=limit)

    async def semantic_search_sentences(
        self, query: str, *, document_id: str | None = None, limit: int
    ) -> list[dict[str, Any]]:
        try:
            rows = await semantic.semantic_search_sentences(
                query, document_id=document_id, limit=limit
            )
        except corpus.ToolCallError as exc:
            # A host without the sentence stack answers `unsupported`; the shared
            # service falls back to lexical retrieval on this signal.
            if exc.kind == "unsupported":
                raise SemanticUnavailable(str(exc)) from exc
            # `not_found` means nothing in scope is embedded. The agent-facing
            # tool raises that rather than return `[]`, because an agent reads an
            # empty list as a finding about the corpus; the BriefingReader port
            # does not — `_probe_semantic` reads empty as `no_vectors` and says
            # so in the pack's gaps, which is a strictly better message than the
            # stack-unavailable one. So the port's contract is honoured here.
            if exc.kind == "not_found":
                return []
            raise
        return [
            {
                "id": row.get("sentenceId"),
                "documentId": row.get("documentId"),
                "ordinal": row.get("ordinal"),
                "text": row.get("text"),
            }
            for row in rows
        ]


_MCP_READER = _MCPBriefingReader()


async def _ranked_passages(
    query_text: str, document_id: str, limit: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Compatibility wrapper around the shared briefing ranking implementation."""
    return await ranked_passages(query_text, document_id, limit, reader=_MCP_READER)


def _clipped_passage_text(sentence: dict[str, Any]) -> str:
    """Compatibility wrapper using the MCP's configurable passage cap."""
    return clipped_passage_text(sentence, _MAX_PASSAGE_CHARS)


async def briefing_pack(
    topic: str,
    max_documents: int = 6,
    passages_per_document: int = 4,
    max_entities: int = 25,
) -> dict[str, Any]:
    """Assemble cited sources, passages, entities, coverage, and gaps for *topic*.

    The response is evidence for an external agent or person to compose from;
    every claim remains untrusted until its citation is checked.
    """
    return await build_briefing_pack(
        topic,
        reader=_MCP_READER,
        max_documents=max_documents,
        passages_per_document=passages_per_document,
        max_entities=max_entities,
        passage_budget_chars=_BRIEFING_PASSAGE_BUDGET,
        max_passage_chars=_MAX_PASSAGE_CHARS,
    )


async def entity_dossier(name: str, max_mentions: int = 40) -> dict[str, Any]:
    """Gather cited mentions and the complete visible-document roster for one named entity."""
    search = await corpus.search_sentences(name, limit=max_mentions)
    coverage = search.get("coverage") or {}
    roster_rows = await corpus.full_text_search(name, limit=_DOSSIER_DOCUMENT_LIMIT)

    cited_count: dict[str, int] = {}
    cited_title: dict[str, str | None] = {}
    cited: list[dict[str, Any]] = []
    for sentence in search["hits"]:
        document_id = str(sentence["documentId"])
        title = sentence.get("title")
        cited_count[document_id] = cited_count.get(document_id, 0) + 1
        cited_title.setdefault(document_id, title)
        cited.append(
            {
                "document_id": document_id,
                "title": title,
                "sentence_ordinal": sentence.get("ordinal"),
                "text": _clipped_passage_text(sentence),
                "cite": _cite(document_id, title, sentence.get("ordinal")),
            }
        )

    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in roster_rows:
        document_id = str(row["documentId"])
        seen.add(document_id)
        documents.append(
            {
                "document_id": document_id,
                "title": row.get("title") or cited_title.get(document_id),
                "rank": row.get("rank"),
                "cited_mention_count": cited_count.get(document_id, 0),
            }
        )
    for document_id, count in cited_count.items():
        if document_id not in seen:
            documents.append(
                {
                    "document_id": document_id,
                    "title": cited_title.get(document_id),
                    "rank": None,
                    "cited_mention_count": count,
                }
            )
    documents.sort(key=lambda doc: (doc["cited_mention_count"], doc["rank"] or 0), reverse=True)

    kept, dropped = _budget_passages(cited, _DOSSIER_MENTION_BUDGET)
    mentions_capped = bool(coverage.get("limit_reached"))
    roster_capped = len(roster_rows) >= _DOSSIER_DOCUMENT_LIMIT
    uncited_documents = sum(1 for doc in documents if doc["cited_mention_count"] == 0)

    gaps: list[str] = []
    if not cited and not documents:
        gaps.append(f"No mentions of “{name}” found in the corpus you can read.")
    if mentions_capped:
        gaps.append(
            f"Cited passages were capped at max_mentions={max_mentions}; more mentions of "
            f"“{name}” exist. Raise max_mentions or scope by document to retrieve the rest."
        )
    if roster_capped:
        gaps.append(
            f"The document roster hit its {_DOSSIER_DOCUMENT_LIMIT}-document cap; more "
            f"documents may contain “{name}”. document_count is a floor, not a total."
        )
    if uncited_documents and not mentions_capped:
        gaps.append(
            f"{uncited_documents} document(s) contain “{name}” but no passage was cited here "
            "(cited_mention_count 0); raise max_mentions to surface their passages."
        )
    if dropped:
        gaps.append(_budget_gap(dropped, "mentions", "lower max_mentions or scope by document"))

    guidance = "State nothing about this entity that a mention above does not support."
    if mentions_capped or roster_capped:
        guidance += (
            " This is a capped sample, not the complete record: do not claim totals or "
            "completeness. cited_mentions counts only the passages shown; document_count is a "
            "floor when truncated is true."
        )

    return {
        "entity": name,
        "retrieval": "literal mention match across the corpus you can read",
        "cited_mentions": len(cited),
        "document_count": len(documents),
        "truncated": mentions_capped or roster_capped,
        "documents": documents,
        "mentions": kept,
        "gaps": gaps,
        "guidance": guidance,
    }


async def document_evidence(document_id: str, focus: str, max_passages: int = 12) -> dict[str, Any]:
    """Pull bounded, verbatim, citable passages about *focus* from one visible document."""
    document, (sentences, terms) = await asyncio.gather(
        corpus.get_document(document_id),
        _ranked_passages(focus, document_id, max_passages),
    )
    title = (document or {}).get("title")
    passages = [
        {
            "sentence_ordinal": sentence.get("ordinal"),
            "text": _clipped_passage_text(sentence),
            "matched_terms": sentence["matched_terms"],
            "cite": _cite(document_id, title, sentence.get("ordinal")),
        }
        for sentence in sentences
    ]
    gaps = (
        [] if passages else [f"No passage in this document matches “{focus}”{_terms_tried(terms)}."]
    )
    passages, dropped = _budget_passages(passages, _EVIDENCE_PASSAGE_BUDGET)
    if dropped:
        gaps.append(_budget_gap(dropped, "passages", "narrow the focus or lower max_passages"))
    return {
        "document_id": document_id,
        "document": document,
        "focus": focus,
        "passages": passages,
        "gaps": gaps,
        "guidance": "Ground every statement about this document in the passages above.",
    }


TOOLS: tuple[Callable[..., Any], ...] = (
    briefing_pack,
    entity_dossier,
    document_evidence,
)
