---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Document lifecycle"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["webapp", "api"]
classification: "public"
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Document lifecycle

This page defines the lifecycle states a Document moves through — Active, Archived, Deleted, and Purged — and states which transitions are reversible.

## Definition

A Document's lifecycle is the set of states that determine whether it appears in your Corpus and whether that appearance can be undone.

- **Active** — the default state. The Document is fully present in your Corpus: searchable, readable, and available to every capability.
- **Archived** — set aside. The Document is removed from default views but is retained in full. Archiving is reversible: restore returns it to `Active`.
- **Deleted** — hidden from the Corpus. The Document no longer appears in searches or listings. Deletion is reversible until the Document is purged: restore returns a deleted-but-not-yet-purged Document to `Active`.
- **Purged** — permanent removal. Once a Document is purged, it is gone and cannot be restored.

Lifecycle state is distinct from **document status**, which reports whether text extraction succeeded during ingestion. A Document's lifecycle can change without changing its status, and its status does not determine its lifecycle.

## Why it exists and when it applies

The lifecycle separates "I want this out of my way" from "I want this gone." Archiving keeps a Document for later without cluttering active work. Deletion removes it from view while leaving a recovery window. Purge is the irreversible end state for a Document that must not come back.

This concept applies whenever a Document seems to be missing. Before concluding data is lost, check whether it is `Archived` or `Deleted` — both are recoverable — versus `Purged`, which is not.

## How it relates to neighboring concepts

- **Corpus and documents** — lifecycle state governs whether a Document is part of your visible Corpus at a given moment.
- **Jobs** — lifecycle transitions are carried out as background work; the transition completes when its Job completes.
- **Backups** — a Backup captures the Corpus at a point in time and is the recovery path for a Document that has been purged, which restore cannot recover.

## Reversibility

| Transition | Reversible | How it reverses |
|---|---|---|
| `Active` → `Archived` | Yes | Restore returns it to `Active`. |
| `Active` → `Deleted` | Yes, until purge | Restore returns it to `Active` while it remains unpurged. |
| `Deleted` → `Purged` | No | Purge is permanent; a Backup is the only recovery path. |

> **Warning.** Deletion is reversible only until the Document is purged. Purge is irreversible: after purge, restore cannot recover the Document, and only a Backup taken before the purge can. Confirm you have a current Backup before purging anything you may need again.

## Limitations

- Purge is permanent and cannot be undone from within the Corpus.
- Destructive lifecycle actions — deleting and archiving a Document — are not exposed on the MCP server; they are performed through the Corpus browser and the GraphQL API. See [Access and roles](./access-and-roles.md) for who may perform them.
- Restore recovers a Document only while it is `Archived` or `Deleted`-but-unpurged. After purge, use a Backup.

## Related how-tos and reference

- How-to: [Archive a document](../how-to/archive-or-delete-a-document.md)
- How-to: [Delete a document](../how-to/archive-or-delete-a-document.md)
- How-to: [Restore a backup](../how-to/back-up-and-restore.md)
- Concept: [Corpus and documents](./corpus-and-documents.md)
- Concept: [Backups](./backups.md)
- Concept: [Jobs](./jobs.md)
- Reference: [GraphQL API](../reference/api.md)
