---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Archive or delete a document"
doc_type: "how-to"
audience: ["end-user", "administrator", "developer"]
surfaces: ["webapp", "api"]
classification: "restricted"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Archive or delete a document

Set a Document aside without removing it (Archive), hide it from the Corpus (Delete), or return either state to Active (Restore).

> **Warning — reversibility.** Archiving and deleting are both reversible: an Archived Document is set aside, and a Deleted Document is hidden from the Corpus and can be restored **until it is Purged**. Purge is irreversible — a Purged Document cannot be restored. This guide covers Archive, Delete, and Restore; it does not purge.

## Prerequisites

- A write grant on the Document, and its id.
- **GraphQL API:** a bearer token with the `write` scope.
- These are document lifecycle actions and are **not** available on the MCP server, which intentionally excludes destructive document verbs.
- Lifecycle state (`Active`, `Archived`, `Deleted`) is separate from document status, which is the ingestion outcome. Archiving and deleting do not change document status.

## Choose the action

- **Archive** — set the Document aside. It stays in the Corpus in the `Archived` state.
- **Delete** — hide the Document from the Corpus (soft delete). It is recoverable until Purged.
- **Restore** — return an Archived or Deleted Document to `Active`.

## Act through the Corpus browser

1. Open the Document (`/doc/<document-id>`) or select it in the Corpus (`/corpus`).
2. Choose Archive or Delete.

   Result: an Archived Document leaves the default Corpus view but remains recoverable; a Deleted Document is hidden from the Corpus.

3. To bring it back, choose Restore.

   Result: the Document returns to the `Active` state and reappears in the Corpus.

## Act through the GraphQL API

Each lifecycle action enqueues a Job. Poll it to `succeeded` with the `job` query.

### Archive

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){archiveDocument(documentId:$id){id kind status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"archiveDocument":{"id":"bbbb1111-...","kind":"archive_document","status":"queued"}}}`.

### Delete (reversible until purge)

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){deleteDocument(documentId:$id){id kind status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"deleteDocument":{"id":"bbbb2222-...","kind":"delete","status":"queued"}}}`. The Document is hidden from the Corpus and recoverable until Purged.

### Restore

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){restoreDocument(documentId:$id){id kind status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"restoreDocument":{"id":"bbbb3333-...","kind":"restore_document","status":"queued"}}}`. Once succeeded, the Document is `Active` again.

## Verification

- Poll the enqueued Job to `succeeded`, then confirm the state:
  - An Archived or Deleted Document no longer appears in the default Corpus listing.
  - After Restore, the Document reappears in the Corpus.

## Common failures

- **Not permitted** — missing `write` scope or write grant on the Document. See [Not permitted](/docs/troubleshooting/not-permitted).
- **Restore returns nothing to restore** — the Document is already `Active`, or it has been Purged and can no longer be recovered.
- **Job reports `failed`** — read the Job's `error`. See [A job failed](/docs/troubleshooting/job-failed).

## Next steps

- [Back up and restore the corpus](/docs/how-to/back-up-and-restore)
- [Share a document](/docs/how-to/share-a-document)
- Concept: [Document lifecycle](/docs/concepts/document-lifecycle)
- Reference: [GraphQL API](/docs/reference/api)
