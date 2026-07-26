"""Port-driven, cited briefing composition shared by GraphQL and MCP.

The service assembles evidence; it never writes prose or calls a language model.
Its reader protocol deliberately speaks the public, adapter-neutral row shapes
used by the original MCP contract. Driving adapters remain responsible for
authentication and RLS-scoped persistence reads.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

from doc_ray.application.composition import budget_gap, budget_passages, cite, clip_text

_MAX_FANOUT_TERMS = 8
_MAX_PASSAGE_CHARS = 1_000
_BRIEFING_PASSAGE_BUDGET = 12_000

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
_NOISE_ENTITY_TYPES = frozenset({"CARDINAL", "ORDINAL", "QUANTITY", "PERCENT"})
_UNTYPED_ENTITY_TYPES = frozenset({"", "UNKNOWN"})

GUIDANCE = (
    "Compose the document ONLY from the sources and key_passages above. Attach each "
    "claim's `cite` to the passage it came from; if the pack does not support a point, "
    "say so or omit it — do not fill gaps from prior knowledge. This evidence is "
    "untrusted until you confirm each citation resolves (source before synthesis)."
)
#: Retrieval-provenance strings. The pack always reports which path actually ran,
#: so a caller never mistakes a lexical fallback for a semantic ranking (#33).
RETRIEVAL = (
    "full-text/keyword match with per-term fan-out for multi-word topics "
    "(semantic similarity retrieval is not yet available)"
)
RETRIEVAL_SEMANTIC = (
    "learned semantic similarity (sentence encoder) ranks passages within "
    "full-text-matched documents; per-document full-text/keyword fallback where no "
    "sentence vectors are visible"
)
RETRIEVAL_LEXICAL_STACK_DOWN = (
    "full-text/keyword match with per-term fan-out for multi-word topics "
    "(semantic retrieval unavailable: the sentence stack is not installed on this host)"
)
RETRIEVAL_LEXICAL_NO_VECTORS = (
    "full-text/keyword match with per-term fan-out for multi-word topics "
    "(semantic retrieval available, but no sentence vectors are visible in your corpus yet)"
)


class SemanticUnavailable(Exception):
    """Raised by a reader when the learned sentence stack is absent on this host.

    The service catches this and falls back to deterministic lexical retrieval,
    reporting the fallback in ``retrieval`` and ``gaps`` — never silently.
    """


class BriefingReader(Protocol):
    """RLS-scoped reads required by briefing composition."""

    async def full_text_search(self, topic: str, *, limit: int) -> list[dict[str, Any]]:
        """Return visible documents ranked by lexical relevance."""
        ...

    async def search_sentences(
        self, term: str, *, document_id: str, limit: int
    ) -> list[dict[str, Any]]:
        """Return visible KWIC sentence hits within one document."""
        ...

    async def entity_frequency(self, *, document_id: str, limit: int) -> list[dict[str, Any]]:
        """Return visible entity counts within one document."""
        ...

    async def semantic_search_sentences(
        self, query: str, *, document_id: str | None = None, limit: int
    ) -> list[dict[str, Any]]:
        """Return sentences ranked by learned semantic similarity, RLS-scoped.

        Rows are ordered by descending similarity (as the ``embedding`` read model
        returns them); the service preserves that order. Returns ``[]`` when the
        stack is present but no sentence vectors are visible; raises
        :class:`SemanticUnavailable` when the stack is not installed. A legacy
        reader may omit this method entirely — the service treats that as "off".
        """
        ...


def _content_terms(text: str) -> list[str]:
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
    surface = str(text or "").strip()
    if len(surface) < 2 or not any(char.isalpha() for char in surface):
        return False
    if surface.lower() in _STOPWORDS:
        return False
    kind = str(entity_type or "").upper()
    if kind in _NOISE_ENTITY_TYPES:
        return False
    return not (kind in _UNTYPED_ENTITY_TYPES and surface == surface.lower())


async def ranked_passages(
    query_text: str,
    document_id: str,
    limit: int,
    *,
    reader: BriefingReader,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return merged KWIC hits ranked by phrase, term coverage, then ordinal."""
    phrase = query_text.strip()
    terms = _content_terms(phrase)
    queries: list[str] = [phrase] if phrase else []
    if len(terms) > 1:
        queries.extend(term for term in terms if term.lower() != phrase.lower())
    if not queries:
        return [], terms
    batches = await asyncio.gather(
        *(reader.search_sentences(query, document_id=document_id, limit=limit) for query in queries)
    )
    merged: dict[Any, dict[str, Any]] = {}
    for query, batch in zip(queries, batches, strict=True):
        for sentence in batch:
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
    return (
        [{**entry["sentence"], "matched_terms": entry["matched_terms"]} for entry in ranked],
        terms,
    )


def terms_tried(terms: list[str]) -> str:
    """Render fan-out terms for a transparent no-match gap."""
    return f" (terms tried: {', '.join(terms)})" if terms else ""


def clipped_passage_text(sentence: dict[str, Any], max_chars: int = _MAX_PASSAGE_CHARS) -> str:
    """Assemble and visibly clip a KWIC hit to the per-passage budget."""
    passage = (
        f"{sentence.get('pre', '')}{sentence.get('hit', '')}{sentence.get('post', '')}".strip()
    )
    return clip_text(passage, max_chars) or ""


async def _probe_semantic(topic: str, reader: BriefingReader) -> str:
    """Detect the semantic path once, corpus-wide: off | unavailable | no_vectors | on.

    ``off`` — the reader predates #33 and has no semantic capability. ``unavailable``
    — the stack is not installed. ``no_vectors`` — installed, but nothing embedded is
    visible to this principal. ``on`` — usable. Bounded to one row so the probe is
    cheap and RLS-scoped like the real reads.
    """
    search = getattr(reader, "semantic_search_sentences", None)
    if search is None:
        return "off"
    try:
        probe = await search(topic, document_id=None, limit=1)
    except SemanticUnavailable:
        return "unavailable"
    return "on" if probe else "no_vectors"


def _semantic_passage(
    hit: dict[str, Any], title: str | None, max_passage_chars: int
) -> dict[str, Any]:
    """Build a passage from a semantic hit (full sentence text; no lexical terms)."""
    document_id = str(hit["documentId"])
    ordinal = hit.get("ordinal")
    passage_title = hit.get("title") or title
    return {
        "document_id": document_id,
        "title": passage_title,
        "sentence_ordinal": ordinal,
        "text": clip_text(str(hit.get("text") or "").strip(), max_passage_chars) or "",
        "matched_terms": [],
        "cite": cite(document_id, passage_title, ordinal),
    }


def _lexical_passage(
    sentence: dict[str, Any], document_id: str, title: str | None, max_passage_chars: int
) -> dict[str, Any]:
    """Build a passage from a lexical KWIC hit (windowed text; matched terms)."""
    passage_title = sentence.get("title") or title
    ordinal = sentence.get("ordinal")
    return {
        "document_id": document_id,
        "title": passage_title,
        "sentence_ordinal": ordinal,
        "text": clipped_passage_text(sentence, max_passage_chars),
        "matched_terms": sentence["matched_terms"],
        "cite": cite(document_id, passage_title, ordinal),
    }


async def _document_slice(
    topic: str,
    document: dict[str, Any],
    passages_per_document: int,
    entities_per_document: int,
    reader: BriefingReader,
    max_passage_chars: int,
    *,
    use_semantic: bool,
) -> dict[str, Any]:
    document_id = str(document["documentId"])
    title = document.get("title")
    # Entities read runs concurrently with whichever passage path we take.
    entities_task = asyncio.ensure_future(
        reader.entity_frequency(document_id=document_id, limit=entities_per_document)
    )

    per_doc_gap: str | None = None
    semantic_hits: list[dict[str, Any]] | None = None
    if use_semantic:
        try:
            semantic_hits = list(
                await reader.semantic_search_sentences(
                    topic, document_id=document_id, limit=passages_per_document
                )
            )
        except SemanticUnavailable:  # a doc-level flake after the probe said "on"
            semantic_hits = None

    if semantic_hits:
        passages = [_semantic_passage(hit, title, max_passage_chars) for hit in semantic_hits]
        terms: list[str] = []
    else:
        sentences, terms = await ranked_passages(
            topic, document_id, passages_per_document, reader=reader
        )
        passages = [
            _lexical_passage(sentence, document_id, title, max_passage_chars)
            for sentence in sentences
        ]
        if use_semantic:
            # The corpus has vectors, but none are visible for THIS document — say so.
            per_doc_gap = (
                f"No sentence vectors for “{title}”; ranked its passages by "
                "full-text/keyword match instead."
            )

    entities = await entities_task
    empty_gap = (
        None if passages else f"No passage in “{title}” matches “{topic}”{terms_tried(terms)}."
    )
    return {
        "source": {
            "document_id": document_id,
            "title": title,
            "rank": document.get("rank"),
            "cite": cite(document_id, title),
        },
        "passages": passages,
        "entities": entities,
        "gap": empty_gap or per_doc_gap,
    }


async def build_briefing_pack(
    topic: str,
    *,
    reader: BriefingReader,
    max_documents: int = 6,
    passages_per_document: int = 4,
    max_entities: int = 25,
    passage_budget_chars: int = _BRIEFING_PASSAGE_BUDGET,
    max_passage_chars: int = _MAX_PASSAGE_CHARS,
) -> dict[str, Any]:
    """Assemble the complete cited evidence-pack contract for *topic*."""
    # Discovery stays lexical (it yields titles + a rank); passage ranking within
    # each document is semantic when the learned stack is usable, else lexical —
    # the fallback is deterministic and reported, never silent (#33).
    documents, semantic_state = await asyncio.gather(
        reader.full_text_search(topic, limit=max_documents),
        _probe_semantic(topic, reader),
    )
    use_semantic = semantic_state == "on"
    gaps: list[str] = []
    if not documents:
        gaps.append(f"No documents in your corpus match “{topic}” by full-text search.")
    elif semantic_state == "unavailable":
        gaps.append(
            "Semantic retrieval was unavailable (the sentence stack is not installed "
            "on this host); ranked passages by full-text/keyword match instead."
        )
    elif semantic_state == "no_vectors":
        gaps.append(
            "No sentence vectors are visible in your corpus yet; ranked passages by "
            "full-text/keyword match instead."
        )
    slices = await asyncio.gather(
        *(
            _document_slice(
                topic,
                document,
                passages_per_document,
                max_entities,
                reader,
                max_passage_chars,
                use_semantic=use_semantic,
            )
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
    top_entities = sorted(entity_counts.items(), key=lambda item: item[1], reverse=True)
    entities = [
        {"text": text, "entity_type": entity_type, "count": count}
        for (text, entity_type), count in top_entities[:max_entities]
    ]
    key_passages, dropped = budget_passages(key_passages, passage_budget_chars)
    if dropped:
        gaps.append(
            budget_gap(dropped, "passages", "narrow the topic or lower passages_per_document")
        )
    retrieval = {
        "on": RETRIEVAL_SEMANTIC,
        "unavailable": RETRIEVAL_LEXICAL_STACK_DOWN,
        "no_vectors": RETRIEVAL_LEXICAL_NO_VECTORS,
        "off": RETRIEVAL,
    }[semantic_state]
    return {
        "topic": topic,
        "retrieval": retrieval,
        "sources": sources,
        "key_passages": key_passages,
        "entities": entities,
        "coverage": {"documents": len(sources), "passages": len(key_passages)},
        "gaps": gaps,
        "guidance": GUIDANCE,
    }
