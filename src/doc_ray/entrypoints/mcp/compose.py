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
roadmap, not yet built). Multi-word topics fan out to their content terms so a
phrase that never occurs verbatim still grounds passages: the full phrase is
searched first and outranks term hits, then sentences matching more distinct
terms, then earlier ordinals. What could not be grounded is reported in ``gaps``
rather than invented.

Everything is pure orchestration over the read tools in
:mod:`doc_ray.entrypoints.mcp.corpus`, so it inherits the same token-forwarded,
RLS-scoped visibility: an agent grounds only in what its principal may read.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from doc_ray.entrypoints.mcp import corpus

#: Cap on distinct terms fanned out per document so one topic cannot amplify
#: into an unbounded number of GraphQL round-trips.
_MAX_FANOUT_TERMS = 8

#: English function words: never searched as fan-out terms and never reported
#: as briefing entities.
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "all",
        "also",
        "am",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "did",
        "do",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "has",
        "have",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "itself",
        "just",
        "me",
        "more",
        "most",
        "my",
        "no",
        "nor",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "ours",
        "out",
        "over",
        "own",
        "same",
        "she",
        "should",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "you",
        "your",
        "yours",
    }
)

#: Entity types that are counts, not names — noise in a briefing's entity list.
_NOISE_ENTITY_TYPES = frozenset({"CARDINAL", "ORDINAL", "QUANTITY", "PERCENT"})

#: Type labels that carry no typing signal; surfaces under them must look like
#: proper nouns to be worth listing.
_UNTYPED_ENTITY_TYPES = frozenset({"", "UNKNOWN"})


def _content_terms(text: str) -> list[str]:
    """Distinct content terms of *text* in order, stopwords and bare numbers dropped."""
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", text):
        lowered = token.lower()
        if len(token) < 2 or token.isdigit() or lowered in _STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        terms.append(token)
    return terms[:_MAX_FANOUT_TERMS]


def _entity_signal(text: str | None, entity_type: str | None) -> bool:
    """Whether an entity row is briefing signal rather than counting/function-word noise."""
    surface = str(text or "").strip()
    if len(surface) < 2 or not any(char.isalpha() for char in surface):
        return False
    if surface.lower() in _STOPWORDS:
        return False
    kind = str(entity_type or "").upper()
    if kind in _NOISE_ENTITY_TYPES:
        return False
    return not (kind in _UNTYPED_ENTITY_TYPES and surface == surface.lower())


async def _ranked_passages(
    query_text: str, document_id: str, limit: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """KWIC-search *query_text* in one document, fanning multi-word queries out per term.

    The full phrase and each distinct content term are searched concurrently and
    the hits merged by sentence. Ranking: phrase matches first, then sentences
    matching more distinct terms, then earlier ordinals.

    Returns:
        A ``(hits, terms)`` tuple — ranked sentence dicts (each annotated with the
        ``matched_terms`` that found it), and the content terms that were tried.
    """
    phrase = query_text.strip()
    terms = _content_terms(phrase)
    queries: list[str] = [phrase] if phrase else []
    if len(terms) > 1:
        queries.extend(term for term in terms if term.lower() != phrase.lower())
    if not queries:
        return [], terms

    batches = await asyncio.gather(
        *(corpus.search_sentences(query, document_id=document_id, limit=limit) for query in queries)
    )
    merged: dict[Any, dict[str, Any]] = {}
    for query, hits in zip(queries, batches, strict=True):
        for sentence in hits:
            key = sentence.get("id") or (sentence.get("documentId"), sentence.get("ordinal"))
            entry = merged.setdefault(key, {"sentence": sentence, "matched_terms": []})
            if query not in entry["matched_terms"]:
                entry["matched_terms"].append(query)

    def rank(entry: dict[str, Any]) -> tuple[int, int, int]:
        matched = entry["matched_terms"]
        ordinal = entry["sentence"].get("ordinal")
        return (
            0 if phrase in matched else 1,
            -sum(1 for match in matched if match != phrase),
            ordinal if isinstance(ordinal, int) else 2**31,
        )

    ranked = sorted(merged.values(), key=rank)[:limit]
    hits = [{**entry["sentence"], "matched_terms": entry["matched_terms"]} for entry in ranked]
    return hits, terms


def _terms_tried(terms: list[str]) -> str:
    """Render the fan-out terms for a gap message; empty when nothing fanned out."""
    return f" (terms tried: {', '.join(terms)})" if terms else ""


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
    (sentences, terms), entities = await asyncio.gather(
        _ranked_passages(topic, document_id, passages_per_document),
        corpus.entity_frequency(document_id=document_id, limit=entities_per_document),
    )
    passages = [
        {
            "document_id": document_id,
            "title": sentence.get("title") or title,
            "sentence_ordinal": sentence.get("ordinal"),
            "text": _passage_text(sentence),
            "matched_terms": sentence["matched_terms"],
            "cite": _cite(document_id, sentence.get("title") or title, sentence.get("ordinal")),
        }
        for sentence in sentences
    ]
    gap = None if passages else f"No passage in “{title}” matches “{topic}”{_terms_tried(terms)}."
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
        topic: The subject to gather evidence for. Multi-word topics also fan out
            to their content terms, so a phrase absent verbatim still grounds
            passages; each passage lists the `matched_terms` that found it.
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
            if not _entity_signal(entity.get("text"), entity.get("entityType")):
                continue
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
        "retrieval": (
            "full-text/keyword match with per-term fan-out for multi-word topics "
            "(semantic similarity retrieval is not yet available)"
        ),
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
        focus: The aspect to pull passages about. Multi-word focuses also fan out
            to their content terms; each passage lists the `matched_terms` that
            found it.
        max_passages: Cap on the cited passages returned.

    Returns:
        A dict with `document`, `focus`, `passages`, `gaps`, and `guidance`.
    """
    document, (sentences, terms) = await asyncio.gather(
        corpus.get_document(document_id),
        _ranked_passages(focus, document_id, max_passages),
    )
    title = (document or {}).get("title")
    passages = [
        {
            "sentence_ordinal": sentence.get("ordinal"),
            "text": _passage_text(sentence),
            "matched_terms": sentence["matched_terms"],
            "cite": _cite(document_id, title, sentence.get("ordinal")),
        }
        for sentence in sentences
    ]
    gaps = (
        [] if passages else [f"No passage in this document matches “{focus}”{_terms_tried(terms)}."]
    )
    return {
        "document_id": document_id,
        "document": document,
        "focus": focus,
        "passages": passages,
        "gaps": gaps,
        "guidance": "Ground every statement about this document in the passages above.",
    }


#: The composition-grounding tools registered on the MCP server (see server.py).
TOOLS: tuple[Callable[..., Any], ...] = (
    briefing_pack,
    entity_dossier,
    document_evidence,
)
