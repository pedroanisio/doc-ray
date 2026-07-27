---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "GraphQL API reference"
doc_type: "reference"
audience: ["developer", "integrator"]
surfaces: ["api"]
classification: "public"
applies_to: "1.43.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# GraphQL API reference

This page is the field-level contract for the Doc-Ray GraphQL API: the endpoint, authentication, the write-enqueues-a-Job model, pagination, and every query and mutation field.

## Endpoint

The GraphQL API is served at `/graphql`. All operations are sent as HTTP `POST` requests with a JSON body carrying `query` and optional `variables`.

Doc-Ray publishes no client SDK in 1.43.0. Use a standard GraphQL client against
this contract or connect through the MCP server; do not treat internal Python
modules as a stable external SDK.

## Authentication

A request presents a bearer token in either header:

```
Authorization: Bearer <token>
```

or

```
X-DocRay-API-Token: <token>
```

- Tokens are mapped to principals through the `DOCRAY_API_TOKENS` configuration variable. Each token grants a principal read or read-and-write scope. See [Configuration](./config.md).
- When `DOCRAY_API_ALLOW_ANONYMOUS` is `true` (the default), an unauthenticated request is accepted and scoped to an anonymous principal. Set it to `false` to require a token on every call.
- A request sees only Documents its principal owns or that are shared with it. No tier grants corpus-wide read except Administrator. See [Access and roles](../concepts/access-and-roles.md).

Redact real tokens and identifiers when sharing examples.

## The write-enqueues-a-Job model

Every mutation that changes corpus content enqueues a background **Job** and returns its status immediately. There is no synchronous pipeline result; poll the returned Job by id with the `job` query until it reaches a terminal status. See [Jobs](../concepts/jobs.md).

- `ingestDocument` with `pipeline: true` (the default) chains annotation, semantic enrichment, and canonicalization automatically.
- `ingestDocument` takes **exactly one** of `file` (a multipart upload) or `ref` (a reference a registered source claims — today arXiv `abs`/`pdf` URLs, `arxiv:<id>`, and bare ids). A reference is fetched by the worker, so the mutation returns as soon as the Job is queued; a reference no source claims is rejected immediately rather than becoming a Job that could only fail.
- Bulk mutations return one Job per affected Document.
- The billing, collection, sharing, seed-decision, and lens mutations return their result object directly rather than a Job, because they do not run the ingestion pipeline.

## Pagination

Listings are **keyset-paginated**. A list field returns objects each carrying a `cursor`. Pass the last `cursor` you received as the `after` argument to fetch the next page. Cursors are per-field and not interchangeable across fields.

## Query fields

### Documents and content

| Field | Purpose | Key arguments |
|---|---|---|
| `documents` | List Documents visible to the caller, newest first. | `limit`, `after`, `docType`, `status`, `tags` |
| `document` | Fetch one Document's metadata, counts, and nested content. | `id` |
| `documentOutline` | Return a Document's addressable ordinal ranges for aimed reads. | `documentId`, `maxEntries` |
| `documentImages` | List detected images and their visual features in a Document. | `documentId`, `limit`, `after` |
| `imageClassSummary` | Return the `(imageClass, count)` distribution for a Document's images. | `documentId` |
| `visualCaptions` | Return a Document's committed, grounded captions. | `documentId`, `limit` |
| `visualReview` | List untrusted Tier-3 caption proposals and their review status. | `documentId`, `status`, `limit`, `after` |
| `documentMetadataHistory` | Return the change log of a Document's metadata fields. | `documentId` |
| `documentMetadataSuggestions` | List generated metadata suggestions for a Document. | `documentId`, `status` |
| `corpusManifest` | Return the shard map for splitting the Corpus into `shardCount` parts. | `shardCount` |
| `corpusShard` | Return one disjoint slice of the Corpus for map-reduce sweeps. | `shardIndex`, `shardCount`, `limit`, `after` |

### Search

| Field | Purpose | Key arguments |
|---|---|---|
| `fullTextSearch` | Rank whole Documents by full-text relevance. | `query`, `limit` |
| `searchSentences` | Keyword-in-context Sentence search across the readable Corpus. | `term`, `documentId`, `limit`, `after` |
| `termFrequency` | Check how common candidate terms are before searching (flood-risk verdict per term). | `terms`, `documentId` |
| `semanticSearchSentences` | Rank Sentences by learned semantic similarity. | `query`, `documentId`, `limit` |
| `crossModalImageSearch` | Find images by meaning from a text query. | `query`, `limit` |
| `lexeme` | Fetch one Lexeme by uid, with its occurrences. | `uid` |
| `lexemes` | Search Lexemes by form, part of speech, language, or Document. | `q`, `pos`, `lang`, `documentId`, `limit`, `after` |

A result whose `coverage` field and `note` report truncation means a common term flooded one region; prefer rare, specific terms. See [Search returns too few results](../troubleshooting/search-returns-too-few-results.md).

`semanticSearchSentences` requires embeddings from the active learned model. An
empty result can mean the visible Documents have not been backfilled; operators
should follow the corpus-embedding runbook rather than substituting legacy
hash-BoW vectors.

### Composition

| Field | Purpose | Key arguments |
|---|---|---|
| `briefingPack` | Build a deterministic, gap-honest evidence pack with cited passages and entity frequencies. | `topic`, `maxDocuments`, `passagesPerDocument`, `maxEntities` |

All three adapters delegate to the same deterministic application service;
GraphQL maps field names to camelCase and the webapp renders the result, while
evidence ordering, budget gaps, and citation content stay aligned. The pack is
evidence for a human or downstream composer; Doc-Ray does not publish generated
narrative as corpus fact.

### Entities and canonicalization

| Field | Purpose | Key arguments |
|---|---|---|
| `entities` | List raw Entity Mentions across the Corpus or one Document. | `documentId`, `entityType`, `limit`, `after` |
| `entityFrequency` | Return raw named-entity frequencies. | `documentId`, `entityType`, `limit`, `after` |
| `entityProfiles` | Return frequencies reconciled to one row per entity, with `typeConsistency`. | `documentId`, `entityType`, `limit` |
| `entityMentionCorrections` | Return a Document's durable human NER corrections. | `documentId` |
| `canonical` | Fetch one Canonical Entity by numeric id. | `id` |
| `canonicalSearch` | Search the canonical registry by name. | `q`, `limit`, `after` |
| `seedSuggestions` | List pending seed candidates for a Document. | `documentId`, `status` |
| `unresolvedMentions` | List a Document's mentions not yet linked to a Canonical Entity. | `documentId`, `limit`, `after` |
| `resolutionReview` | List resolution decisions awaiting review. | `documentId`, `status`, `limit`, `after` |
| `documentClassification` | Read a Document's genre classification (fiction / non-fiction / mixed) and its review status. | `documentId` |
| `entityProjection` | Read how a Document's mentions resolve under a Lens (local, global, or explicitly unresolved). | `documentId`, `lensId`, `limit`, `after` |
| `lensEntities` | Significant entities for a Lens: mention/document volume, type profile, staleness stamp. | `lensId`, `limit`, `after`, `minMentions` |
| `entityRelations` | Sentence-level co-occurrence pairs for a Lens with normalized strength. | `lensId`, `canonicalId`, `limit`, `after`, `minStrength` |
| `relatedEntities` | Explore related entities a bounded number of hops out (over-cap requests are rejected). | `lensId`, `canonicalId`, `hops`, `limit` |
| `allusions` | Typed cross-lens links (alludes to, depicts, …) readable only when both endpoint Lenses and the evidence are visible. | `canonicalId`, `lensId`, `direction`, `limit`, `after` |

Prefer `entityProfiles` over `entityFrequency` for "who/what": a low `typeConsistency` means the type is contested, not that the entity is unimportant. See [Entities and canonicalization](../concepts/entities-and-canonicalization.md). Relationship strength from `entityRelations` / `relatedEntities` is a co-occurrence measure with a `refreshedAt` staleness stamp — a lead to verify against cited sentences, not a claim of a real-world relationship.

### Collections and lenses

| Field | Purpose | Key arguments |
|---|---|---|
| `collections` | List Collections the caller can see. | `limit`, `after`, `kind` |
| `collection` | Fetch one Collection with its member Documents. | `id` |
| `lenses` | List Collections marked as entity-resolution scopes. | `limit`, `after` |

Since 1.46.0 a designated Lens also scopes entity identity through projection (see `entityProjection` and [Lenses](../concepts/lenses.md)).

### Jobs

| Field | Purpose | Key arguments |
|---|---|---|
| `job` | Poll one Job's status by id. | `id` |
| `jobs` | List Jobs, optionally for one Document. | `documentId`, `limit`, `after` |

### Sharing

| Field | Purpose | Key arguments |
|---|---|---|
| `documentShares` | List the principals a Document is shared with. | `id` |

### Backups

| Field | Purpose | Key arguments |
|---|---|---|
| `backupEnabled` | Whether the in-app backup feature toggle is on for this deployment. | — |

Backups are read through Job status; there is no standalone backup-listing query. Create and restore Backups with the mutations below. See [Backups](../concepts/backups.md).

### Billing

| Field | Purpose | Key arguments |
|---|---|---|
| `billingStatus` | Return the tenant's subscription and configuration status. | — |
| `billingEvents` | List recent billing events. | `limit` |

### Audit

| Field | Purpose | Key arguments |
|---|---|---|
| `auditEvents` | List security audit events, filterable by actor, action, target, time, or source. | `actor`, `action`, `targetType`, `targetId`, `occurredAfter`, `occurredBefore`, `sourceIp`, `limit`, `after` |

### Service

| Field | Purpose | Key arguments |
|---|---|---|
| `version` | Return the running product version. | — |

## Mutation fields

Every content mutation returns a Job unless noted. Poll the returned Job with the `job` query.

### Documents and metadata

| Field | Purpose | Key arguments |
|---|---|---|
| `ingestDocument` | Ingest a Document from an upload **or** a remote reference (exactly one of `file`/`ref`); chains the pipeline when `pipeline` is true. | `file`, `ref`, `title`, `pipeline` |
| `reextractDocument` | Re-run text extraction for one Document. | `documentId` |
| `reextractDocuments` | Re-run extraction across Documents. | `onlyMissingGeometry` |
| `updateDocumentMetadata` | Edit a Document's title, description, tags, or authors. | `documentId`, `title`, `description`, `tags`, `authors` |
| `bulkTagDocuments` | Merge tags into several Documents. | `documentIds`, `tags` |
| `suggestDocumentMetadata` | Generate metadata suggestions for a Document. | `documentId` |
| `decideDocumentMetadataSuggestions` | Approve or reject metadata suggestions. Returns the count updated. | `approveIds`, `rejectIds` |

### Annotation and enrichment

| Field | Purpose | Key arguments |
|---|---|---|
| `annotateDocument` | Run first-stage annotation for a Document. | `documentId`, `lang` |
| `reannotate` | Re-run the full linguistic annotation. | `documentId`, `lang` |
| `semanticEnrichDocument` | Run semantic enrichment for a Document. | `documentId` |
| `embedDocumentSentences` | Enqueue learned sentence embedding for one Document. | `documentId` |
| `embedCorpusSentences` | Enqueue one learned-embedding Job per visible Document, optionally only where the active model is missing. Returns a Job list. | `onlyMissing` |
| `canonicalizeDocument` | Canonicalize a Document; `resolve` enables auto-linking. | `documentId`, `resolve` |

### Entities and canonicalization

| Field | Purpose | Key arguments |
|---|---|---|
| `correctEntityMention` | Record a durable human fix to an NER mention. | `documentId`, `action`, `charStart`, `charEnd`, `entityType`, `surface`, `newCharStart`, `newCharEnd`, `originalType`, `rationale` |
| `suggestSeeds` | Mine seed candidates for a Document. | `documentId`, `limit` |
| `decideSeeds` | Approve or reject seed suggestions. Returns the count updated. | `approveIds`, `rejectIds` |
| `seedApproved` | Seed a Document's approved suggestions into the registry. | `documentId` |

Canonicalization links mentions only to entities already in the registry; seed first (`suggestSeeds` → `decideSeeds` → `seedApproved`). The `action` argument of `correctEntityMention` is one of `reclassify`, `reject`, `add`, or `adjust_span`. See [Correct an entity](../how-to/correct-an-entity.md).

### Genre, projections, and allusions

| Field | Purpose | Key arguments |
|---|---|---|
| `classifyDocument` | Enqueue the deterministic genre classifier; the guess stays pending until confirmed. | `documentId` |
| `confirmDocumentClassification` | Humanly confirm a Document's genre. | `documentId`, `genre`, `subtype`, `rationale` |
| `confirmProjection` | Confirm how a mention resolved under a Lens (survives re-annotation). | `mentionId`, `lensId` |
| `rejectProjection` | Reject a mention's resolution under a Lens. | `mentionId`, `lensId` |
| `proposeAllusion` | Propose a typed cross-lens link with evidence; idempotent. | `fromCanonicalId`, `fromLensId`, `toCanonicalId`, `toLensId`, `edgeType`, `evidenceDocumentId`, `confidence`, `rationale` |
| `confirmAllusion` | Confirm a proposed allusion. | `id` |
| `rejectAllusion` | Reject a proposed allusion. | `id` |

An allusion is never an identity claim and is never transitive; it links two entities while preserving both.

### Collections

| Field | Purpose | Key arguments |
|---|---|---|
| `createCollection` | Create a Collection. Returns the Collection. | `name`, `description`, `kind`, `rule`, `docType`, `status`, `tags` |
| `updateCollection` | Rename a Collection or edit its description. Returns the Collection. | `collectionId`, `name`, `description` |
| `deleteCollection` | Delete a Collection container. Returns a boolean. | `collectionId` |
| `addDocumentToCollection` | Add a Document to a manual Collection. Returns the Collection. | `collectionId`, `documentId` |
| `removeDocumentFromCollection` | Remove a Document from a manual Collection. Returns the Collection. | `collectionId`, `documentId` |

### Lenses

| Field | Purpose | Key arguments |
|---|---|---|
| `promoteCollectionToLens` | Promote a Collection to a named entity-resolution scope. Returns the Collection. | `collectionId`, `parentLensId` |
| `demoteLens` | Demote a Lens back to an ordinary Collection. Returns the Collection. | `collectionId` |
| `setDocumentDefaultLens` | Set or clear a Document's default Lens. Returns the Document. | `documentId`, `lensId` |
| `setLensProjects` | Designate whether a Lens materializes entity projections. | `lensId`, `projects` |

The Global lens cannot be renamed, demoted, or deleted.

### Visual review

| Field | Purpose | Key arguments |
|---|---|---|
| `decideVisualCaptions` | Approve or reject Tier-3 caption proposals; returns the number decided. | `approveIds`, `rejectIds` |

An id cannot appear in both lists, and the combined batch is capped. Approval
commits the caption for `visualCaptions`; rejection keeps it out of trusted
reads. The authenticated reviewer and decision are audited.

### Lifecycle

| Field | Purpose | Key arguments |
|---|---|---|
| `archiveDocument` | Archive a Document (reversible). | `documentId` |
| `bulkArchiveDocuments` | Archive several Documents. Returns one Job per Document. | `documentIds` |
| `deleteDocument` | Delete a Document; hidden and reversible until purged. | `documentId` |
| `restoreDocument` | Restore an archived or deleted Document. | `documentId` |

Deleting a Document hides it from the Corpus and is reversible until purge. See [Document lifecycle](../concepts/document-lifecycle.md).

### Sharing

| Field | Purpose | Key arguments |
|---|---|---|
| `shareDocument` | Share a Document with a principal by email. Returns the share. | `documentId`, `email`, `permission` |
| `revokeDocumentShare` | Revoke a Document share. Returns the count revoked. | `documentId`, `email`, `permission` |
| `setDocumentVisibility` | Set a Document's visibility. Returns the new visibility. | `documentId`, `visibility` |

Sharing exposes a Document to another principal. Confirm the recipient before granting.

### Jobs

| Field | Purpose | Key arguments |
|---|---|---|
| `cancelJob` | Request cancellation of a Job. | `jobId`, `reason` |

### Backups

| Field | Purpose | Key arguments |
|---|---|---|
| `createBackup` | Create a Backup. | `note` |
| `restoreBackup` | Restore the Corpus from a Backup. | `backupId` |

Restoring a Backup replaces current corpus state. Confirm the target Backup before restoring; this action is not silently reversible. See [Backups](../concepts/backups.md).

### Billing

| Field | Purpose | Key arguments |
|---|---|---|
| `createBillingCheckoutSession` | Start a checkout session. Returns a session URL. | `successUrl`, `cancelUrl` |
| `createBillingPortalSession` | Open the billing portal. Returns a session URL. | `returnUrl` |
| `retryBillingEvent` | Reprocess a billing event by id. Returns the event. | `stripeEventId` |

## Example query

List the ten newest Documents, then page with the returned cursor:

```graphql
query RecentDocuments {
  documents(limit: 10) {
    id
    title
    status
    lifecycleState
    cursor
  }
}
```

The last `cursor` in the result is the `after` value for the next page:

```graphql
query NextPage($after: String!) {
  documents(limit: 10, after: $after) {
    id
    title
    cursor
  }
}
```

## Example mutation

Ingest a Document with the full pipeline, then poll the returned Job:

```graphql
mutation Ingest($file: Upload!) {
  ingestDocument(file: $file, pipeline: true) {
    id
    kind
    status
  }
}
```

Response (identifier redacted):

```json
{
  "data": {
    "ingestDocument": {
      "id": "00000000-0000-0000-0000-000000000000",
      "kind": "ingest",
      "status": "queued"
    }
  }
}
```

Ingest by reference instead — no upload, no multipart:

```graphql
mutation IngestRef($ref: String!) {
  ingestDocument(ref: $ref, pipeline: true) {
    id
    kind
    status
  }
}
```

```json
{ "ref": "https://arxiv.org/abs/2401.00001" }
```

The Job's `result` carries the new `documentId` once the worker has fetched and
extracted the document.

Poll the Job until it reaches a terminal status:

```graphql
query Poll($id: ID!) {
  job(id: $id) {
    status
    progressStage
    error
  }
}
```

## Related concepts, how-tos, and reference

- Concept: [Corpus and documents](../concepts/corpus-and-documents.md)
- Concept: [Jobs](../concepts/jobs.md)
- Concept: [Entities and canonicalization](../concepts/entities-and-canonicalization.md)
- Concept: [Access and roles](../concepts/access-and-roles.md)
- How-to: [Ingest a document](../how-to/ingest-a-document.md)
- How-to: [Correct an entity](../how-to/correct-an-entity.md)
- Reference: [MCP server](./mcp.md)
- Reference: [Configuration](./config.md)
- Troubleshooting: [Search returns too few results](../troubleshooting/search-returns-too-few-results.md)
</content>
</invoke>
