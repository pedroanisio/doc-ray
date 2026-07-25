---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Backups"
doc_type: "concept"
audience: ["end-user", "evaluator", "administrator"]
surfaces: ["cli", "api", "config"]
classification: "public"
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Backups

This page defines a Backup, explains what it captures and how retention works, and describes restore at a conceptual level.

## Definition

A **Backup** is a point-in-time capture of the Corpus and everything Doc-Ray holds about it: the ingested Documents, their derived layers, the entity registry, Collections, and the metadata that ties them together. A Backup is the recovery path for data that the lifecycle cannot recover on its own — in particular, a Document that has been purged.

## Why it exists and when it applies

The document lifecycle offers recovery within limits: an `Archived` or `Deleted`-but-unpurged Document can be restored. A **Purged** Document cannot. A Backup is what recovers state past that boundary — after a purge, a mistaken bulk change, or data loss — by returning the Corpus to how it stood when the Backup was taken.

Backups can be created on demand and are also taken automatically on a schedule.

## Retention

Doc-Ray keeps a bounded set of Backups rather than every Backup forever:

- **Cadence.** Automatic Backups are taken on a fixed interval, controlled by `DOCRAY_BACKUP_SCHEDULE_SECONDS` (default `86400`, one per day).
- **Newest kept.** The newest `DOCRAY_BACKUP_RETENTION_COUNT` Backups (default `7`) are always retained.
- **Age pruning.** Beyond the newest kept set, Backups older than `DOCRAY_BACKUP_RETENTION_DAYS` (default `30`) are pruned.

The count floor and the age limit work together: the most recent Backups are always kept even when they are older than the age limit.

## Restore, conceptually

Restore returns the Corpus to the state captured in a chosen Backup. It is the recovery mechanism a Backup exists for.

> **Warning.** Restoring a Backup overwrites current state with the Backup's state; changes made after the Backup was taken are not preserved by the restore. Restore is an administrator action with system-wide consequence. Confirm the chosen Backup is the intended one, and understand what will be replaced, before restoring. The step-by-step procedure is in the how-to.

## How it relates to neighboring concepts

- **Document lifecycle** — a Backup recovers past the purge boundary, where restore-within-lifecycle cannot.
- **Corpus and documents** — a Backup captures the whole Corpus and its derived material, not a single Document in isolation.
- **Jobs** — creating and restoring a Backup runs as background work.
- **Configuration** — cadence and retention are set through the environment variables above.

## Limitations

- Restore overwrites current state; work done after the Backup was taken is not merged in.
- Retention is bounded. A Backup older than the kept count and beyond the age limit is pruned and cannot be relied on for recovery.
- Creating and restoring Backups are administrator and operator actions; they are not exposed on the MCP server.

## Related how-tos and reference

- How-to: [Create a backup](../how-to/back-up-and-restore.md)
- How-to: [Restore a backup](../how-to/back-up-and-restore.md)
- Concept: [Document lifecycle](./document-lifecycle.md)
- Concept: [Corpus and documents](./corpus-and-documents.md)
- Concept: [Jobs](./jobs.md)
- Reference: [Configuration](../reference/config.md)
- Reference: [Operator CLI](../reference/cli.md)
