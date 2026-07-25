---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Back up and restore the corpus"
doc_type: "how-to"
audience: ["administrator", "operator"]
surfaces: ["webapp", "api", "config"]
classification: "restricted"
applies_to: "1.42.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Back up and restore the corpus

!!! warning "Requires `DOCRAY_BACKUP_ENABLED=true`"

    The backup subsystem is off by default. With it off, the Backups screen is hidden, `/backups` returns *Not Found*, and `createBackup` / `restoreBackup` are refused with a message saying the subsystem is disabled — not a permissions error. Check `backupEnabled` in the API, or ask your administrator, before following this guide.

    On a managed database, database recovery is handled by the provider instead. See [Backups](../concepts/backups.md).

Create a full Backup of the Corpus, review the Backups catalogue, and restore the whole Corpus from a catalogued Backup.

> **Warning — restore overwrites.** Restoring from a Backup rewrites the entire Corpus from the archive: it replaces current content with the archive's content. It is not a per-Document undo and it is not additive. Create a fresh Backup before restoring, and confirm the target is the intended Backup.

## Prerequisites

- The **Administrator** tier. Creating and restoring Backups is Administrator-only.
- **GraphQL API:** a bearer token with the `write` scope on an Administrator principal.
- A restore requires the target to be at the archive's schema version.

## Retention

Automatic Backups and retention are governed by Configuration:

- `DOCRAY_BACKUP_SCHEDULE_SECONDS` (default `86400`) — the automatic Backup cadence.
- `DOCRAY_BACKUP_RETENTION_COUNT` (default `7`) — the newest N Backups are always kept.
- `DOCRAY_BACKUP_RETENTION_DAYS` (default `30`) — Backups older than this are pruned beyond the kept N.

The newest `DOCRAY_BACKUP_RETENTION_COUNT` Backups are retained regardless of age; older archives beyond that count are pruned once they pass `DOCRAY_BACKUP_RETENTION_DAYS`.

## Back up and restore through the Corpus browser

1. Open the **Backups** screen (`/backups`).

   Result: the screen lists catalogued Backups, each with its creation time, size, Document and blob counts, and any note.

2. Create a Backup.

   Result: a Backup Job is enqueued and the browser follows its status page. On completion, the new archive appears at the top of the Backups list.

3. To restore, select a Backup's restore action.

   Result: a restore Job is enqueued; on completion the Corpus matches the chosen archive. This overwrites current content.

## Create a Backup through the GraphQL API

`createBackup` enqueues a full Backup Job (a database dump plus the object-store blobs) and returns the Job. An optional `note` labels the archive.

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation{createBackup(note:\"pre-migration snapshot\"){id kind status}}"}'
```

Result: `{"data":{"createBackup":{"id":"cccc1111-...","kind":"backup","status":"queued"}}}`. Poll the Job to `succeeded`.

## Restore from a Backup through the GraphQL API

Take the target Backup's id from the Backups screen, then:

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){restoreBackup(backupId:$id){id kind status}}","variables":{"id":"REDACTED_BACKUP_ID"}}'
```

Result: `{"data":{"restoreBackup":{"id":"cccc2222-...","kind":"restore","status":"queued"}}}`. Poll the Job to `succeeded`.

## Verification

- Poll the Backup or restore Job to `succeeded` with the `job` query; a `failed` Job carries the reason in its `error`.
- After a create, the new Backup appears in the Backups screen with a non-zero Document and blob count.
- After a restore, spot-check that expected Documents are present in the Corpus.

## Common failures

- **Not permitted** — the principal is not an Administrator. See [Not permitted](/docs/troubleshooting/not-permitted).
- **Restore refused on a schema-version mismatch** — the target is not at the archive's schema version. See [Restore version mismatch](/docs/troubleshooting/restore-version-mismatch).
- **Job reports `failed`** — read the Job's `error`. See [A job failed](/docs/troubleshooting/job-failed).

## Next steps

- [Archive or delete a document](/docs/how-to/archive-or-delete-a-document)
- Concept: [Backup](/docs/concepts/backups)
- Reference: [Configuration](/docs/reference/configuration), [GraphQL API](/docs/reference/api), [Operator CLI](/docs/reference/cli)
