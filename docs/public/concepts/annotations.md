---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Annotations"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Annotations

This page defines the linguistic layers Doc-Ray builds over a Document — Annotation, Sentence, Span, and Lexeme — at the level a reader needs to use them.

## Definition

When a Document is ingested, Doc-Ray reads its text and builds a set of linguistic layers over it. Four nouns describe what results:

- **Sentence** — a single addressable unit of text within a Document. Sentences are the unit Doc-Ray returns from a read or a search, and each one carries a citation back to its exact place in the Document.
- **Span** — a marked range within the text: a start and end position covering a stretch of characters. A Span is how Doc-Ray points at "this word" or "this name" precisely, rather than at a whole Sentence.
- **Lexeme** — a distinct word or term treated as a searchable unit, independent of where it appears. A Lexeme lets you find every place a term is used and read each occurrence in context.
- **Annotation** — the linguistic information attached to the text across several layers. Annotation is what turns raw text into something queryable: it is how Sentences, Spans, Entity Mentions, and Lexemes are identified and connected.

## Why it exists and when it applies

Annotation is what makes a Document more than stored text. Because every result is a Sentence with a citation, and every mark is a Span with exact boundaries, you can trace any claim back to the precise place it came from. This concept applies whenever you read, search, or cite: the unit you receive is a Sentence, the precise location is a Span, and the searchable term is a Lexeme.

## How it relates to neighboring concepts

- **Corpus and documents** — these layers are built inside each Document; they are the derived material that makes a Document searchable.
- **Entities and canonicalization** — an Entity Mention is anchored to a Span within a Sentence; entity detection is one of the annotation layers.
- **Jobs** — annotation runs as background work during ingestion, before semantic enrichment and canonicalization.

## Limitations

- Annotation is derived from the extracted text. When extraction fails or is partial — reflected in the document status — the layers over that Document are correspondingly limited.
- A search returns Sentences, not whole Documents. To cover a Document completely, read its Sentences by range or sequentially rather than relying on search.
- A common, high-frequency term can flood one region of a Document and starve coverage of the rest. When that happens, the result reports it. Prefer rare, specific terms; a sparse result is usually a query problem, not missing content.

## Related how-tos and reference

- How-to: [Read a document](../how-to/read-a-document.md)
- How-to: [Search the corpus](../how-to/search-the-corpus.md)
- Concept: [Corpus and documents](./corpus-and-documents.md)
- Concept: [Entities and canonicalization](./entities-and-canonicalization.md)
- Concept: [Jobs](./jobs.md)
- Reference: [GraphQL API](../reference/api.md)
- Reference: [MCP server](../reference/mcp.md)
