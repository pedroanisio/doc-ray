"""Composition-grounding MCP tools: assemble cited evidence for a connected agent.

These tools turn the corpus into a briefing an agent can *compose from* — they do
NOT write prose. A client such as Claude Code calls :func:`briefing_pack` for a
topic, receives a structured bundle of sources, cited passages, and entities, and
writes the executive briefing or board deck itself. doc-ray stays the grounded
substrate; the agent supplies the creativity.

This is deliberate, and it enforces PURPOSE.md's three lines:

* **Compose outside it** — nothing here writes to the library; the output is raw
  evidence the agent composes into a *standalone* document.
* **Source before synthesis** — every returned item carries a ``cite`` handle
  (document + sentence), so each claim the agent makes can be traced to a span.
* **AI proposes, verification decides** — these tools only return what is provably
  in the corpus. The ``guidance`` field tells the agent its draft is untrusted
  until every citation is checked.

Retrieval today is full-text / keyword match (semantic similarity is on the
roadmap, not yet built), so tools find sources by term, not by meaning, and report
what they could not ground in ``gaps`` rather than inventing coverage.

Everything is pure orchestration over the read tools in
:mod:`doc_ray.entrypoints.mcp.corpus`, so it inherits the same token-forwarded,
RLS-scoped visibility: an agent grounds only in what its principal may read.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from doc_ray.entrypoints.mcp import corpus


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


def _passage_text(sentence: dict[str, Any]) -> str:
    """Reassemble a KWIC hit (pre + hit + post) into one readable passage."""
    return f"{sentence.get('pre', '')}{sentence.get('hit', '')}{sentence.get('post', '')}".strip()


_BRIEFING_GUIDANCE = (
    "Compose the document ONLY from the sources and key_passages above. Attach each "
    "claim's `cite` to the passage it came from; if the pack does not support a point, "
    "say so or omit it — do not fill gaps from prior knowledge. This evidence is "
    "untrusted until you confirm each citation resolves (source before synthesis)."
)


async def _document_slice(
    topic: str, document: dict[str, Any], passages_per_document: int, entities_per_document: int
) -> dict[str, Any]:
    """Gather one source's cited passages and entities for a briefing pack."""
    document_id = str(document["documentId"])
    title = document.get("title")
    sentences, entities = await asyncio.gather(
        corpus.search_sentences(topic, document_id=document_id, limit=passages_per_document),
        corpus.entity_frequency(document_id=document_id, limit=entities_per_document),
    )
    passages = [
        {
            "document_id": document_id,
            "title": sentence.get("title") or title,
            "sentence_ordinal": sentence.get("ordinal"),
            "text": _passage_text(sentence),
            "cite": _cite(document_id, sentence.get("title") or title, sentence.get("ordinal")),
        }
        for sentence in sentences
    ]
    gap = None if passages else f"No passage in “{title}” literally matches “{topic}”."
    return {
        "source": {
            "document_id": document_id,
            "title": title,
            "rank": document.get("rank"),
            "cite": _cite(document_id, title),
        },
        "passages": passages,
        "entities": entities,
        "gap": gap,
    }


async def briefing_pack(
    topic: str,
    max_documents: int = 6,
    passages_per_document: int = 4,
    max_entities: int = 25,
) -> dict[str, Any]:
    """Assemble a cited evidence pack on *topic* for an agent to compose from.

    Use this to ground an executive briefing, board deck, or literature summary in
    the corpus. It finds the most relevant documents (full-text search), pulls their
    key passages in context, and gathers the entities those documents mention — every
    item carrying a `cite` handle back to a document and sentence.

    This tool writes NO prose and calls NO language model. Compose the document
    yourself from the returned `sources` and `key_passages`, citing each claim; treat
    anything not present here as unsupported, and read `gaps` for what the corpus
    could not ground.

    Args:
        topic: The subject to gather evidence for (matched by keyword/full-text today).
        max_documents: Most relevant documents to draw from.
        passages_per_document: Cited passages to pull from each document.
        max_entities: Cap on the merged entity list returned.

    Returns:
        A dict with `topic`, `sources`, `key_passages`, `entities`, `coverage`,
        `gaps`, and `guidance` — a bundle to compose a grounded document from.
    """
    documents = await corpus.full_text_search(topic, limit=max_documents)
    gaps: list[str] = []
    if not documents:
        gaps.append(f"No documents in your corpus match “{topic}” by full-text search.")

    slices = await asyncio.gather(
        *(
            _document_slice(topic, document, passages_per_document, max_entities)
            for document in documents
        )
    )

    sources: list[dict[str, Any]] = []
    key_passages: list[dict[str, Any]] = []
    entity_counts: dict[tuple[str, str | None], int] = {}
    for part in slices:
        sources.append(part["source"])
        key_passages.extend(part["passages"])
        if part["gap"]:
            gaps.append(part["gap"])
        for entity in part["entities"]:
            key = (str(entity.get("text")), entity.get("entityType"))
            entity_counts[key] = entity_counts.get(key, 0) + int(entity.get("count") or 0)

    # Sort the (key, int-count) pairs first, then shape into dicts — keeps the sort
    # key strictly `int` for the type checker.
    top_entities = sorted(entity_counts.items(), key=lambda item: item[1], reverse=True)
    entities = [
        {"text": text, "entity_type": entity_type, "count": count}
        for (text, entity_type), count in top_entities[:max_entities]
    ]

    return {
        "topic": topic,
        "retrieval": "full-text/keyword match (semantic similarity retrieval is not yet available)",
        "sources": sources,
        "key_passages": key_passages,
        "entities": entities,
        "coverage": {"documents": len(sources), "passages": len(key_passages)},
        "gaps": gaps,
        "guidance": _BRIEFING_GUIDANCE,
    }


async def entity_dossier(name: str, max_mentions: int = 40) -> dict[str, Any]:
    """Gather everything the corpus says about an entity, as cited mentions.

    Use this to ground a profile of a person, place, date, or organization: it
    returns each place the name appears, in context, with a `cite` to the document
    and sentence, plus a per-document mention tally. Cite every fact you state about
    the entity to a mention below; do not infer beyond them.

    Args:
        name: The entity name to look up (literal mention match across the corpus).
        max_mentions: Cap on the cited mentions returned.

    Returns:
        A dict with `entity`, `total_mentions`, `documents`, `mentions`, `gaps`,
        and `guidance`.
    """
    mentions = await corpus.search_sentences(name, limit=max_mentions)
    by_document: dict[str, dict[str, Any]] = {}
    cited: list[dict[str, Any]] = []
    for sentence in mentions:
        document_id = str(sentence["documentId"])
        title = sentence.get("title")
        entry = by_document.setdefault(
            document_id, {"document_id": document_id, "title": title, "mention_count": 0}
        )
        entry["mention_count"] += 1
        cited.append(
            {
                "document_id": document_id,
                "title": title,
                "sentence_ordinal": sentence.get("ordinal"),
                "text": _passage_text(sentence),
                "cite": _cite(document_id, title, sentence.get("ordinal")),
            }
        )
    return {
        "entity": name,
        "retrieval": "literal mention match across the corpus you can read",
        "total_mentions": len(cited),
        "documents": sorted(
            by_document.values(), key=lambda doc: doc["mention_count"], reverse=True
        ),
        "mentions": cited,
        "gaps": [] if cited else [f"No mentions of “{name}” found in the corpus you can read."],
        "guidance": "State nothing about this entity that a mention above does not support.",
    }


async def document_evidence(document_id: str, focus: str, max_passages: int = 12) -> dict[str, Any]:
    """Pull cited passages on *focus* from one document, for grounded summarization.

    Use this to summarize or quote a single paper: it returns the document's metadata
    plus the passages that match `focus`, each with a `cite`. Ground every statement
    about the document in these passages.

    Args:
        document_id: The document to read from.
        focus: The aspect to pull passages about (keyword/full-text within the doc).
        max_passages: Cap on the cited passages returned.

    Returns:
        A dict with `document`, `focus`, `passages`, `gaps`, and `guidance`.
    """
    document, sentences = await asyncio.gather(
        corpus.get_document(document_id),
        corpus.search_sentences(focus, document_id=document_id, limit=max_passages),
    )
    title = (document or {}).get("title")
    passages = [
        {
            "sentence_ordinal": sentence.get("ordinal"),
            "text": _passage_text(sentence),
            "cite": _cite(document_id, title, sentence.get("ordinal")),
        }
        for sentence in sentences
    ]
    return {
        "document_id": document_id,
        "document": document,
        "focus": focus,
        "passages": passages,
        "gaps": [] if passages else [f"No passage in this document matches “{focus}”."],
        "guidance": "Ground every statement about this document in the passages above.",
    }


#: The composition-grounding tools registered on the MCP server (see server.py).
TOOLS: tuple[Callable[..., Any], ...] = (
    briefing_pack,
    entity_dossier,
    document_evidence,
)
