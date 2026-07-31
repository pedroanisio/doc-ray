---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Corpus and documents"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.1"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Corpus and documents

This page defines the two foundational nouns in Doc-Ray — the Corpus and the Document — and separates a document's ingestion status from its lifecycle state.

## Definition

A **Document** is a single ingested file — PDF, EPUB, HTML, Markdown, plain text, DOCX, PPTX, or SVG — together with everything Doc-Ray derives from it: its extracted text, its Sentences, its Annotations, the Entity Mentions found inside it, and its metadata. The original bytes are retained; the derived material is what makes the document searchable and analyzable.

A **Corpus** is the set of Documents visible to you. It is not a single global pool: a Corpus is scoped to a principal. You see the Documents you own and the Documents shared with you; an Administrator sees all. There is no tier that grants corpus-wide read.

**Document status** and **lifecycle state** are two different ideas that are easy to confuse:

- **Document status** is the ingestion outcome — whether text extraction succeeded. It answers "did Doc-Ray read this file?"
- **Lifecycle state** is `Active`, `Archived`, or `Deleted` (and, after purge, gone). It answers "where does this document sit in its life, and who can see it now?"

A document can be fully `Active` in its lifecycle yet have a failed extraction status, and vice versa. Keep the two separate when reasoning about a document.

## Why it exists and when it applies

Every other capability in Doc-Ray is anchored to Documents inside a Corpus. Search returns cited Sentences from Documents. Entity resolution operates over the Entity Mentions inside Documents. Collections group Documents. Because visibility is scoped per principal, the Corpus is also the unit of access: what you can search, read, and curate is exactly your Corpus.

This concept applies whenever a result, count, or listing looks incomplete. A missing document is usually a visibility boundary (it is not in your Corpus) or a lifecycle state (it is `Archived` or `Deleted`), not lost data.

## How it relates to neighboring concepts

- **Collections** group Documents into named sets within your Corpus.
- **Lenses** name a scope of your Corpus; today a Lens is metadata only and does not change what a Document contains or how its entities resolve.
- **Annotations, Sentences, and Spans** are the derived linguistic layers inside each Document.
- **Entity Mentions** are the people, places, dates, and organizations found inside a Document; canonicalization links them to shared Canonical Entities.
- **Jobs** carry out the ingestion and enrichment of a Document; a document's status reflects the outcome of that work.
- **The document lifecycle** governs whether a Document is `Active`, `Archived`, or `Deleted` within your Corpus.

## Limitations

- The Corpus is owner-scoped. No access tier grants corpus-wide read; visibility is owner plus shared plus Administrator.
- A Document is retrieved as cited slices, never as a single whole-document dump. Reading it means reading its Sentences by range or sequentially.
- Document status is not lifecycle state. A successful extraction does not make a document `Active`, and an `Active` document may have failed extraction.
- Supported inputs are PDF, EPUB, HTML, Markdown, plain text, DOCX, PPTX, and SVG. Other formats are not ingested.

## Related how-tos and reference

- How-to: [Ingest a document](../how-to/ingest-a-document.md)
- How-to: [Read a document](../how-to/read-a-document.md)
- How-to: [Search the corpus](../how-to/search-the-corpus.md)
- Concept: [Document lifecycle](./document-lifecycle.md)
- Concept: [Collections](./collections.md)
- Concept: [Access and roles](./access-and-roles.md)
- Reference: [GraphQL API](../reference/api.md)
- Reference: [MCP server](../reference/mcp.md)
