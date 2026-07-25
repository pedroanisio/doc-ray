---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Jobs"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["api", "mcp"]
classification: "public"
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Jobs

This page defines a Job and explains that every write on the GraphQL API runs as a background Job you poll, not as a synchronous result.

## Definition

A **Job** is a unit of background work. When you write to the Corpus through the GraphQL API — ingesting a Document, annotating, enriching, canonicalizing, and similar actions — the write does not complete inline. It enqueues a Job and returns that Job's status. The work runs in the background; you learn its outcome by polling the Job until it finishes.

## Why it exists and when it applies

The work behind a write — reading a file, building the annotation layers, resolving entities — takes real time. Running it as a Job means the write returns immediately with a handle to track, rather than blocking until everything finishes. This concept applies to every API write: there is no synchronous pipeline result to wait on inline. Submit the write, take the returned Job, and poll it.

## The ingest pipeline chain

Ingesting a Document with the pipeline enabled does not stop at extraction. It chains the downstream stages automatically, in order:

1. **Annotate** — build the linguistic layers over the extracted text.
2. **Semantic enrichment** — derive the semantic material over those layers.
3. **Canonicalization** — link Entity Mentions to already-seeded Canonical Entities.

Each stage runs as background work under the ingestion. Poll the Job to follow the Document from ingestion through to a resolved, enriched state. Because canonicalization links only to seeded entities, seed the registry first for that final stage to have targets.

## How it relates to neighboring concepts

- **Corpus and documents** — a Document's status reflects the outcome of the ingestion Job that produced it.
- **Annotations** and **entities and canonicalization** — these are the stages the ingest Job chains through.
- **Document lifecycle** — lifecycle transitions are also carried out as background work and complete when their Job completes.
- **Backups** — creating and restoring a Backup runs as background work as well.

## Limitations

- API writes are asynchronous. Treat the returned Job status as a handle to poll, not a completed result.
- A Job can fail. A failed ingestion is reflected in the Document's status; poll the Job to see the outcome before assuming success.
- A running or queued Job can be cancelled through the GraphQL API. Cancelling a Job is not exposed on the MCP server.

## Related how-tos and reference

- How-to: [Track a job](../how-to/ingest-a-document.md)
- How-to: [Ingest a document](../how-to/ingest-a-document.md)
- Concept: [Corpus and documents](./corpus-and-documents.md)
- Concept: [Entities and canonicalization](./entities-and-canonicalization.md)
- Reference: [GraphQL API](../reference/api.md)
- Reference: [MCP server](../reference/mcp.md)
