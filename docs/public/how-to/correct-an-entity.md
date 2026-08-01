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
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Correct an entity

Record a durable, attributable fix to one machine-produced Entity Mention or to
every exact surface/current-type match in selected Documents, so the correction
survives every future re-annotation.

## Prerequisites

- A write grant on the Document. Corrections are document-scoped writes.
- **GraphQL API:** a bearer token with the `write` scope.
- **MCP:** the write tools are registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`. The default MCP surface is read-only, so `correct_entity_mention` and `correct_entity_surface` are absent unless that flag is on.
- For a span correction, the **character offsets** of the target Span. Read them from a Sentence's `charStart`/`charEnd` in [Read a document](/docs/how-to/read-a-document) or from a Sentence-search hit. A surface rule uses no span offsets.
- A correction is a fix to unverified model output. It is recorded with the type the model originally produced and your rationale, so every fix stays attributable and reversible.

## The six correction actions

Four actions target one stable Span:

- `reclassify` — the Span is a real entity of a different type; pass the correct `entity_type`.
- `reject` — the Span is not an entity; it is dropped.
- `add` — a missed entity; pass its `entity_type` (and `surface`).
- `adjust_span` — the entity is real but its boundaries are wrong; pass the corrected `new_char_start`/`new_char_end`. Pass `entity_type` too to retype it in the same correction, or omit it to preserve the current type.

Two actions target every exact, case-sensitive `surface` + current-type match in
each explicitly selected Document:

- `reclassify_surface` — replace `original_type` with a distinct `entity_type`.
- `reject_surface` — remove every mention that still has the exact `surface` and `original_type`.

Surface actions use `NULL` span coordinates and replay in the same chronological
log as span actions. Later corrections win.

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

4. For a repeated `Apple`/`ORG` error, preview the exact matches in one Document:

   ```
   correct_entity_surface  surface="Apple"  original_type="ORG"  action="reclassify_surface"  entity_type="PRODUCT"  document_id="22222222-2222-2222-2222-222222222222"
   ```

   The default is `dry_run=true`. The result includes per-Document bounded counts,
   `countCapped`, and sentence-context samples, but no Jobs. Verify those samples,
   then repeat with `dry_run=false` to append the rule and enqueue one
   `apply_corrections` Job per Document.

   Pass `collection_id` instead of `document_id` to target a Collection. The tool
   refuses the request if the Collection is empty, invisible, reaches the
   unpageable 200-member bound, or its `documentCount` does not prove the returned
   member list is complete.

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

For a systematic rule, preview an explicit Document list first:

```graphql
query {
  entitySurfaceOccurrences(
    documentIds: ["22222222-2222-2222-2222-222222222222"]
    surface: "Apple"
    originalType: "ORG"
  ) {
    documentId occurrenceCount countCap countCapped
    samples { charStart charEnd sentenceOrdinal sentenceContext }
  }
}
```

After verifying the samples, call `correctEntitySurface` with the same
`documentIds`, `surface`, and `originalType`. Use action
`reclassify_surface` with a distinct `entityType`, or `reject_surface`. The
mutation authorizes every Document before the first correction write and returns
one Job per Document.

## Verification

- The correction log lists the new entry with your `action`, `originalType`, and `rationale`.
- After the job succeeds, entity profiles for the Document reflect the corrected type. Re-run [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about) to confirm.
- For a surface rule, `charStart` and `charEnd` are `null`, while `surfaceOriginalType` records the exact current-type matcher.

## Common failures

- **The MCP correction tools are not offered** — the deployment has not set `DOCRAY_MCP_ENABLE_WRITE`; the MCP surface is read-only. See [MCP write tools unavailable](/docs/troubleshooting/mcp-write-disabled).
- **Not permitted** — the token lacks the `write` scope or a write grant on the Document. See [Not permitted](/docs/troubleshooting/not-permitted).
- **The correction targets the wrong text** — the offsets are wrong. Re-read the exact `charStart`/`charEnd` before correcting.
- **Collection membership is not provably complete** — the MCP tool will not silently correct only the first page. Pass one `document_id` or split the Collection below 200 members.

## Next steps

- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about)
- [Seed canonical entities](/docs/how-to/seed-canonical-entities)
- Concept: [Entity Mention](/docs/concepts/entity-mentions), [Annotation](/docs/concepts/annotations)
- Reference: [MCP server](/docs/reference/mcp), [GraphQL API](/docs/reference/api)
