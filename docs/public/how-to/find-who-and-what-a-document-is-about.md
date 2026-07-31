---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Find who and what a document is about"
doc_type: "how-to"
audience: ["end-user", "integrator"]
surfaces: ["mcp", "api"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Find who and what a document is about

Identify the people, places, dates, and organizations a Document is about using reconciled entity profiles, which merge one entity's split types and spellings and report the disagreement as `typeConsistency`.

## Prerequisites

- Read access to the Document, and its id.
- **MCP:** an MCP client pointed at `/mcp` with a bearer token. `entity_profiles` is a read tool and is always available.
- Prefer entity profiles over raw entity frequency. Raw NER splits a single entity across types and spellings; profiles merge them into one row per entity and measure the split, so a contested type reads as a low `typeConsistency` rather than as noise.

## Read entity profiles over MCP

`entity_profiles` returns one row per entity: `mentions` (the real total), `surfaceForms` (the spellings folded in), `typeSpread` (every type with its count), `dominantType`, `typeConsistency`, and `resolved` (whether the canonical registry backs the entity).

1. List the top entities for the Document:

   ```
   entity_profiles  document_id="22222222-2222-2222-2222-222222222222"  limit=25
   ```

   Result:

   ```json
   [
     {"surface":"Arrakis","normalizedForm":"arrakis","mentions":546,
      "dominantType":"LOC","typeSpread":[{"entityType":"LOC","count":225},{"entityType":"GPE","count":150},{"entityType":"PERSON","count":95},{"entityType":"NORP","count":76}],
      "typeConsistency":0.41,"surfaceForms":["Arrakis"],"canonicalId":null,"canonicalName":null,"wikidataId":null,"resolved":false}
   ]
   ```

2. Read `typeConsistency`. It is the dominant type's share of the mentions, not a model score: `1.0` means the type is never contradicted; `0.41` means the type is **contested**, not that the entity is unimportant.

   Result: for a contested entity, confirm the type with a Sentence search before asserting what it is.

3. Narrow to one entity type when you want a specific cast — for example only organizations:

   ```
   entity_profiles  document_id="22222222-2222-2222-2222-222222222222"  entity_type="ORG"  limit=25
   ```

   Result: rows whose `dominantType` is `ORG`, most-mentioned first.

## Read entity profiles over the GraphQL API

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" \
  -H "content-type: application/json" \
  -d '{"query":"query($d:ID){entityProfiles(documentId:$d,limit:25){surface mentions dominantType typeConsistency surfaceForms resolved typeSpread{entityType count}}}","variables":{"d":"22222222-2222-2222-2222-222222222222"}}'
```

Result:

```json
{"data":{"entityProfiles":[
  {"surface":"Arrakis","mentions":546,"dominantType":"LOC","typeConsistency":0.41,
   "surfaceForms":["Arrakis"],"resolved":false,
   "typeSpread":[{"entityType":"LOC","count":225},{"entityType":"GPE","count":150}]}
]}}
```

## Verification

- Each `typeSpread` sums to `mentions`, and `typeConsistency` equals the dominant type's count divided by `mentions`.
- Only normalization-equivalent spellings fold together; distinct names stay on separate rows. A `resolved: true` row carries a `canonicalName`, confirming the canonical registry backs it.

## Common failures

- **A contested type (low `typeConsistency`)** — the annotator disagreed with itself. Corroborate with [Search the corpus](/docs/how-to/search-the-corpus) before asserting the type; correct it durably with [Correct an entity](/docs/how-to/correct-an-entity).
- **`resolved: false` everywhere** — the Document's entities are reconciled NER only, not yet linked to canonical identities. Populate the registry with [Seed canonical entities](/docs/how-to/seed-canonical-entities).
- **Empty result** — the id is malformed or the Document is not readable. See [Not found](/docs/troubleshooting/not-found).

## Next steps

- [Correct an entity](/docs/how-to/correct-an-entity)
- [Seed canonical entities](/docs/how-to/seed-canonical-entities)
- Concept: [Canonical Entity](/docs/concepts/entities-and-canonicalization), [Entity Mention](/docs/concepts/entity-mentions)
- Reference: [MCP server](/docs/reference/mcp), [GraphQL API](/docs/reference/api)
