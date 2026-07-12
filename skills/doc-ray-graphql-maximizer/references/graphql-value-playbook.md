# GraphQL Value Playbook

Use this reference after `SKILL.md` has triggered and the task needs concrete
queries, workflows, or API-development checks.

## Endpoint and Auth

- GraphQL route: `http://localhost:${API_PORT:-8000}/graphql`.
- Liveness: `/health`.
- Dependency readiness: `/readyz`.
- Backup download proxy: `/backups/{backup_id}/download`.
- Auth: bearer token or `X-DocRay-API-Token`, mapped by `DOCRAY_API_TOKENS`.
- Anonymous requests may be allowed locally but are RLS-scoped through
  `docray_api` with no `app.principal_id`.
- The Flask webapp can show a larger corpus than anonymous GraphQL when
  `DOCRAY_PRINCIPAL_ID` is unset, because the webapp's local/dev connection
  bypasses RLS. Treat that as an operator surface, not proof that anonymous API
  reads should see the same rows.

Minimal curl shape:

```bash
curl -sS http://localhost:8000/graphql \
  -H 'content-type: application/json' \
  -H "X-DocRay-API-Token: $DOCRAY_API_TOKEN" \
  --data '{"query":"{ version }"}'
```

Never print configured token values. To check whether auth is available, use a
boolean/env-shape check rather than dumping `DOCRAY_API_TOKENS`.

## Discovery Workflow

Start broad, then narrow.

For a direct "list all documents" request, prefer the deterministic helper over
handwritten shell snippets:

```bash
python3 skills/doc-ray-graphql-maximizer/scripts/list_documents.py \
  --env-file .env \
  --auth first-configured-token
```

Use `--json` when downstream tooling needs structured output. Use
`--auth anonymous` when proving anonymous/RLS visibility.

```graphql
query Docs($limit: Int!, $after: String) {
  documents(limit: $limit, after: $after) {
    id title docType language status pageCount charCount sentenceCount
    tokenCount authors tags cursor
  }
}
```

```graphql
query Find($q: String!) {
  fullTextSearch(query: $q, limit: 10) { documentId title rank }
  searchSentences(term: $q, limit: 10) {
    id documentId title ordinal pre hit post cursor
  }
}
```

Use `fullTextSearch` to find relevant documents. Use `searchSentences` for
citable sentence-level evidence.

### Empty Corpus Diagnostic

If `documents` returns `[]`, classify the result before answering.

1. Confirm the API is alive and ready with `/health` and `/readyz`.
2. Record whether the GraphQL request was anonymous or authenticated. Use
   `scripts/list_documents.py --auth anonymous` and, if a token is available,
   re-run with `--auth first-configured-token`.
3. If the webapp or operator says documents exist, compare visibility layers:
   - GraphQL anonymous: `docray_api` with empty `app.principal_id`.
   - GraphQL token: `docray_api` with mapped principal id and scopes.
   - Webapp with no `DOCRAY_PRINCIPAL_ID`: local/dev superuser passthrough that
     bypasses RLS.
4. Use read-only SQL only for troubleshooting, and label it as database/operator
   evidence rather than API evidence:

```sql
SELECT id, title, doc_type, language, status, page_count, char_count
FROM documents
ORDER BY created_at DESC;

SET ROLE docray_api;
SELECT current_user,
       current_setting('app.principal_id', true) AS principal,
       count(*) AS visible_documents
FROM documents;
RESET ROLE;

SELECT p.id, p.display_name, p.kind, r.name AS role
FROM principals p
LEFT JOIN principal_roles pr ON pr.principal_id = p.id
LEFT JOIN roles r ON r.id = pr.role_id
ORDER BY p.created_at, p.display_name;

SELECT document_id, principal_id, permission
FROM document_acl
ORDER BY document_id, principal_id, permission;
```

Conclusion rule: report "no documents visible to this GraphQL caller" unless an
authenticated/admin GraphQL query or RLS-aware SQL check proves the corpus is
actually empty.

## Document Drill-Down

Use this after choosing a document ID.

```graphql
query DocumentEvidence($id: ID!, $limit: Int!, $after: String) {
  document(id: $id) {
    id title docType language status error originalFilename sourceUri
    pageCount charCount byteSize contentSha256 createdAt updatedAt
    authors tags metadata { key value }
    provenance { id kind tool toolVersion occurredAt actor note params }
    sentences(limit: $limit, after: $after) {
      id ordinal text charStart charEnd cursor
    }
    annotationRuns(limit: 20) { id layer tool toolVersion createdAt cursor }
  }
}
```

For title, description, and tag edits, read the audit trail separately:

```graphql
query MetadataHistory($id: ID!) {
  documentMetadataHistory(documentId: $id) {
    field oldValue newValue changedAt actor
  }
}
```

For annotation layers:

```graphql
query Layers($id: ID!, $limit: Int!, $after: String) {
  document(id: $id) {
    tokens(limit: $limit, after: $after) {
      id ordinal surface upos xpos lexemeUid charStart charEnd
      features { feature value }
      cursor
    }
    dependencies(limit: $limit, after: $after) {
      annotationId dependentTokenId headTokenId deprel cursor
    }
    chunks(limit: $limit, after: $after) {
      id annotationId chunkType surface charStart charEnd cursor
    }
    corefChains(limit: 20) {
      id annotationId mentions { spanId surface } cursor
    }
    entityLinks(limit: $limit, after: $after) {
      mentionId scheme kbId confidence entityType surface charStart charEnd cursor
    }
  }
}
```

Value rule: return spans and surfaces together. Spans let another agent verify
or reconstruct context; surfaces make the answer readable.

## Lexeme and KWIC Analysis

```graphql
query Lexemes($q: String, $pos: String, $doc: ID, $after: String) {
  lexemes(q: $q, pos: $pos, documentId: $doc, limit: 25, after: $after) {
    uid lang pos lemma uses cursor
    occurrences(documentId: $doc, limit: 5) {
      documentId title charStart surface pre hit post cursor
    }
  }
}
```

Use lexeme UIDs when tracking a concept across documents. Use occurrence
snippets when the answer needs human-inspectable evidence.

## Entity and Canonicalization Workflow

Entity mentions are observed spans. Canonical entities are resolved identities.
Keep them separate.

```graphql
query Entities($doc: ID, $type: String) {
  entityFrequency(documentId: $doc, entityType: $type, limit: 50) {
    text entityType count cursor
  }
  entities(documentId: $doc, entityType: $type, limit: 50) {
    id documentId title entityType text charStart charEnd cursor
  }
}
```

```graphql
query CanonicalWork($doc: ID!, $q: String!) {
  canonicalSearch(q: $q, limit: 10) {
    id preferred entityType wikidataId description
    forms { formSurface lang formType confidence occurrences }
    cursor
  }
  unresolvedMentions(documentId: $doc, limit: 25) {
    mentionId surface entityType charStart charEnd cursor
  }
  resolutionReview(documentId: $doc, limit: 25) {
    id mentionId status confidence reason candidates
    proposedCanonical { id preferred entityType wikidataId }
    cursor
  }
}
```

For domain seeding:

```graphql
query SeedAudit($doc: ID!) {
  seedSuggestions(documentId: $doc) {
    id term occurrences source qid label description entityType status
  }
}
```

High-value output includes wrong-identity risk: surface, proposed QID/label,
description, and why it should be approved or rejected.

## Write and Job Workflows

Use mutations only when requested. Every mutation either returns a job or, for
seed decisions, a row count.

```graphql
mutation Upload($file: Upload!, $title: String, $pipeline: Boolean!) {
  ingestDocument(file: $file, title: $title, pipeline: $pipeline) {
    id kind status documentId error result
  }
}
```

Pipeline defaults to:

1. ingest;
2. annotate;
3. semantic enrichment;
4. canonicalize.

Operational mutations:

```graphql
mutation Ops($doc: ID!) {
  annotateDocument(documentId: $doc) { id kind status }
  semanticEnrichDocument(documentId: $doc) { id kind status }
  canonicalizeDocument(documentId: $doc, resolve: false) { id kind status }
}
```

Resolver cost rule: `canonicalizeDocument(resolve: true)` can invoke frontier
resolution for residue; keep it explicit.

Metadata edits:

```graphql
mutation UpdateMetadata($doc: ID!) {
  updateDocumentMetadata(
    documentId: $doc
    title: "Renamed document"
    tags: ["reviewed"]
  ) {
    id kind status documentId
  }
}
```

Only passed fields change. A completed `update_metadata` job records changed
title, description, and tag values in `documentMetadataHistory`.

Seed curation:

```graphql
mutation Curate($approve: [ID!], $reject: [ID!], $doc: ID!) {
  decideSeeds(approveIds: $approve, rejectIds: $reject)
  seedApproved(documentId: $doc) { id kind status }
}
```

Job polling — request timing and progress, not just status:

```graphql
query Job($id: ID!, $doc: ID) {
  job(id: $id) {
    id kind status documentId error result
    createdAt startedAt finishedAt attempts maxAttempts
    progressCurrent progressTotal progressStage
  }
  jobs(documentId: $doc, limit: 20) {
    id kind status documentId error result cursor
    progressCurrent progressTotal progressStage
  }
}
```

Terminal statuses are the only completion evidence. Follow up with the relevant
read query to prove the desired artifact landed. Interpretation notes:

- `progressCurrent/progressTotal/progressStage` advance through named phases
  while a job runs (e.g. annotate reports seven phases); `attempts > 1` means
  the job was reclaimed/retried after a worker loss.
- A `running` job whose heartbeat is minutes old is a stalled corpse awaiting
  the reaper, not active work — check `startedAt` against wall time.
- Instead of polling, the API also streams job transitions and progress as
  Server-Sent Events: `GET /jobs/events` (same host as `/graphql`; events are
  RLS-scoped to the caller). Use it for long pipelines when the environment
  can hold a connection open.

Reprocessing an existing document (all return jobs; `reextract*` is PDF
geometry backfill):

```graphql
mutation Reprocess($doc: ID!) {
  reannotate(documentId: $doc, lang: "en") { id kind status }
  reextractDocument(documentId: $doc) { id kind status }
}
mutation BackfillCorpus {
  # Admin-only, corpus-wide: one job per PDF; pass false to redo all PDFs.
  reextractDocuments(onlyMissingGeometry: true) { id kind status }
}
```

Job control (admin scope): `cancelJob(jobId: $id)` cancels a queued or running
job without marking it succeeded or advancing its pipeline chain.

Lifecycle (reversible; `deleteDocument` is a soft delete since BL-059 — the
row and blob remain restorable):

```graphql
mutation Lifecycle($doc: ID!, $docs: [ID!]!) {
  archiveDocument(documentId: $doc) { id kind status }
  restoreDocument(documentId: $doc) { id kind status }
  bulkArchiveDocuments(documentIds: $docs) { id kind status }
  bulkTagDocuments(documentIds: $docs, tags: ["reviewed"]) { id kind status }
}
```

Archived/deleted documents drop out of default corpus listings — another
"empty result" cause to rule out alongside RLS scope.

Metadata suggestions (verified model output; never auto-applied):

```graphql
mutation Suggest($doc: ID!) {
  suggestDocumentMetadata(documentId: $doc) { id kind status }
}
query ReviewSuggestions($doc: ID!) {
  documentMetadataSuggestions(documentId: $doc) {
    id field suggestedValue confidence status
  }
}
mutation Decide($approve: [ID!], $reject: [ID!]) {
  decideDocumentMetadataSuggestions(approveIds: $approve, rejectIds: $reject)
}
```

Security audit trail (admin scope, append-only; distinct from
`document.provenance`, which is pipeline history):

```graphql
query Audit {
  auditEvents(limit: 50) { id action actorPrincipalId targetId occurredAt }
}
```

Overlay readiness: `document.hasPositionData` reports whether PDF page
geometry exists (drives the webapp's Original view; false means enqueue
`reextractDocument`, not that extraction failed).

## API Development Checks

When extending GraphQL:

1. Add or update read-adapter functions before resolver logic when possible.
2. Keep resolvers thin: clamp page size through `ctx.page_size(limit)`, delegate
   to adapters, map rows to types.
3. Add cursor pagination for list fields.
4. Preserve RLS/auth expectations; writes call `_require_write`.
5. Add focused tests:
   - `src/doc_ray/tests/test_api_contract.py` for surface contracts.
   - `src/doc_ray/tests/test_api.py` for general read behavior.
   - `src/doc_ray/tests/test_api_layers.py` for annotation layers.
   - `src/doc_ray/tests/test_api_canonical_surfaces.py` for canonicalization.
   - `src/doc_ray/tests/test_api_pipeline.py` for mutation/job workflows.
6. Re-run `python skills/doc-ray-graphql-maximizer/scripts/schema_digest.py`
   and include changed fields in the final evidence.
