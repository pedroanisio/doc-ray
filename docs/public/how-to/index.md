---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "How-to guides"
doc_type: "how-to"
audience: ["end-user", "administrator", "integrator", "developer", "operator"]
surfaces: ["webapp", "api", "mcp", "config"]
classification: "public"
applies_to: "1.43.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# How-to guides

Task-focused guides for working with Doc-Ray across the Corpus browser, the GraphQL API, and the MCP server. Each guide states its prerequisites, shows the expected result of every consequential step, and links the failures you might hit.

## Build your corpus

- [Ingest a document](/docs/how-to/ingest-a-document) — upload a file and run it through the ingestion pipeline; poll the Job to completion.

## Find and read

- [Search the corpus](/docs/how-to/search-the-corpus) — pick a Document with full-text search, then find passages with keyword-in-context Sentence search and read the coverage signal.
- [Read a document](/docs/how-to/read-a-document) — map a Document into ordinal ranges, then read any range or the whole Document verbatim with citation handles.
- [Build a cited briefing](/docs/how-to/build-a-cited-briefing) — assemble a
  deterministic evidence pack, verify each passage, and preserve its citation.

## Understand entities

- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about) — read reconciled entity profiles and interpret `typeConsistency`.
- [Correct an entity](/docs/how-to/correct-an-entity) — record a durable fix to an Entity Mention with one of the four correction actions.
- [Seed canonical entities](/docs/how-to/seed-canonical-entities) — mine, curate, seed, and canonicalize to populate the canonical registry.

## Organize the corpus

- [Manage collections](/docs/how-to/manage-collections) — create manual and smart Collections, add and remove Documents, rename, and delete.
- [Organize with lenses](/docs/how-to/organize-with-lenses) — promote a Collection to a Lens and set a Document's default Lens (metadata only today).
- [Edit document metadata](/docs/how-to/edit-document-metadata) — edit title, description, tags, and authors, and read the change history.

## Share, archive, and protect

- [Share a document](/docs/how-to/share-a-document) — grant or revoke read/annotate access and publish to the organization.
- [Archive or delete a document](/docs/how-to/archive-or-delete-a-document) — Archive, Delete, and Restore across the document lifecycle.
- [Back up and restore the corpus](/docs/how-to/back-up-and-restore) — create Backups, review the catalogue, and restore the whole Corpus.

## Integrate an agent

- [Connect an agent over MCP](/docs/how-to/connect-an-agent-over-mcp) — point an MCP client at `/mcp`, authenticate, and enable write tools.

## Related documentation

- Concepts: [Corpus, Documents, Collections, Lenses, Canonical Entities, Jobs, and Backups](/docs/concepts)
- Reference: [GraphQL API](/docs/reference/api), [MCP server](/docs/reference/mcp), [Operator CLI](/docs/reference/cli), [Configuration](/docs/reference/configuration)
- [Troubleshooting](/docs/troubleshooting)
