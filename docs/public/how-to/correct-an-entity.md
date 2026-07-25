---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Correct an entity"
doc_type: "how-to"
audience: ["end-user", "integrator", "developer"]
surfaces: ["webapp", "mcp", "api"]
classification: "public"
applies_to: "1.43.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Correct an entity

Record a durable, attributable fix to a machine-produced Entity Mention — reclassify its type, reject it, add a missed one, or adjust its boundaries — so the correction survives every future re-annotation.

## Prerequisites

- A write grant on the Document. Corrections are document-scoped writes.
- **GraphQL API:** a bearer token with the `write` scope.
- **MCP:** the write tools are registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`. The default MCP surface is read-only, so `correct_entity_mention` is absent unless that flag is on.
- The **character offsets** of the target Span. Read them from a Sentence's `charStart`/`charEnd` in [Read a document](/docs/how-to/read-a-document) or from a Sentence-search hit.
- A correction is a fix to unverified model output. It is recorded with the type the model originally produced and your rationale, so every fix stays attributable and reversible.

## The four correction actions

- `reclassify` — the Span is a real entity of a different type; pass the correct `entity_type`.
- `reject` — the Span is not an entity; it is dropped.
- `add` — a missed entity; pass its `entity_type` (and `surface`).
- `adjust_span` — the entity is real but its boundaries are wrong; pass the corrected `new_char_start`/`new_char_end`.

## Correct an Entity in the review inbox

1. Open **Review** (`/review`) and choose the **Entities** lane.
2. Optionally filter to one Document, then inspect the source Sentence and
   character offsets before deciding.
3. Choose the correction action, supply the corrected type or surface where the
   action requires it, and submit.

   Result: the page reports the queued correction Job. The durable correction is
   applied through the audited GraphQL write path and will be reconciled again
   after future annotation runs.

The Metadata and Visual lanes use the same inbox envelope but different
decision mutations. Do not approve an Entity change merely because a model
proposed it; verify the source passage first.

## Correct an entity over MCP

Each correction enqueues a small job that re-applies the correction log onto the current mentions. Poll it with `get_job`.

1. Reclassify a Span the model typed as `PERSON` that is really a place:

   ```
   correct_entity_mention  document_id="22222222-2222-2222-2222-222222222222"  action="reclassify"  char_start=6801  char_end=6808  entity_type="GPE"  original_type="PERSON"  rationale="Place name, not a person"
   ```

   Result:

   ```json
   {"id":"44444444-4444-4444-4444-444444444444","kind":"apply_corrections","status":"queued","documentId":"22222222-2222-2222-2222-222222222222","error":null}
   ```

2. Poll the job until it reports `succeeded`:

   ```
   get_job  job_id="44444444-4444-4444-4444-444444444444"
   ```

   Result: `{"id":"44444444-4444-4444-4444-444444444444","status":"succeeded","error":null}`.

3. Read the correction log to confirm the fix and to see what was already corrected before recording a new one:

   ```
   entity_mention_corrections  document_id="22222222-2222-2222-2222-222222222222"
   ```

   Result:

   ```json
   [
     {"action":"reclassify","charStart":6801,"charEnd":6808,"entityType":"GPE","surface":null,
      "newCharStart":null,"newCharEnd":null,"originalType":"PERSON","rationale":"Place name, not a person","createdAt":"2026-07-25T14:02:11Z"}
   ]
   ```

## Correct an entity over the GraphQL API

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" \
  -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){correctEntityMention(documentId:$id,action:\"reject\",charStart:12040,charEnd:12046){id kind status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result:

```json
{"data":{"correctEntityMention":{"id":"55555555-5555-5555-5555-555555555555","kind":"apply_corrections","status":"queued"}}}
```

Read the log back with the `entityMentionCorrections` query.

## Verification

- The correction log lists the new entry with your `action`, `originalType`, and `rationale`.
- After the job succeeds, entity profiles for the Document reflect the corrected type. Re-run [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about) to confirm.

## Common failures

- **`correct_entity_mention` is not offered by the MCP server** — the deployment has not set `DOCRAY_MCP_ENABLE_WRITE`; the MCP surface is read-only. See [MCP write tools unavailable](/docs/troubleshooting/mcp-write-disabled).
- **Not permitted** — the token lacks the `write` scope or a write grant on the Document. See [Not permitted](/docs/troubleshooting/not-permitted).
- **The correction targets the wrong text** — the offsets are wrong. Re-read the exact `charStart`/`charEnd` before correcting.

## Next steps

- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about)
- [Seed canonical entities](/docs/how-to/seed-canonical-entities)
- Concept: [Entity Mention](/docs/concepts/entity-mentions), [Annotation](/docs/concepts/annotations)
- Reference: [MCP server](/docs/reference/mcp), [GraphQL API](/docs/reference/api)
