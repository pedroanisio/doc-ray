---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Seed canonical entities"
doc_type: "how-to"
audience: ["end-user", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Seed canonical entities

Populate the canonical registry for a Document by mining seed candidates, curating which to approve, seeding the approved ones, then canonicalizing — because canonicalization links mentions only to entities already in the registry.

## Prerequisites

- A write grant on the Document, and its id.
- **GraphQL API:** a bearer token with the `write` scope.
- **MCP:** the write tools (`suggest_seeds`, `decide_seeds`, `seed_approved`, `canonicalize_document`) are registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`.
- Order matters. Canonicalization links only to already-seeded entities, so seed the registry **before** canonicalizing. Nothing reaches the registry without an explicit approval.

The workflow is four ordered actions: `suggestSeeds` → review and `decideSeeds` → `seedApproved` → `canonicalizeDocument`.

## Curate through the Corpus browser

1. Open the Document's seeding screen at `/doc/<document-id>/seeding`.

   Result: the screen lists mined candidates. If none are present yet, mine them first (step below), then reload.

2. Approve the candidates that name a correct identity and reject the rest.

   Result: approved candidates are marked for seeding; nothing is written to the registry yet.

3. Seed the approved candidates, then canonicalize the Document.

   Result: approved identities enter the registry, and canonicalization links the Document's mentions to them.

## Curate through the GraphQL API

### Step 1 — Mine candidates

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){suggestSeeds(documentId:$id,limit:40){id status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"suggestSeeds":{"id":"66666666-...","status":"queued"}}}`. Poll the job to `succeeded`. Candidates land as `pending` suggestions.

### Step 2 — Review the pending suggestions

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"query($id:ID!){seedSuggestions(documentId:$id,status:\"pending\"){id term qid label description entityType status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result:

```json
{"data":{"seedSuggestions":[
  {"id":"aaaaaaaa-0001","term":"Arrakis","qid":"Q3708","label":"Arrakis","description":"fictional desert planet","entityType":"LOC","status":"pending"},
  {"id":"aaaaaaaa-0002","term":"Guild","qid":"Q99","label":"trade guild","description":"unrelated real-world guild","entityType":"ORG","status":"pending"}
]}}
```

Read each `description` to catch a wrong-identity resolution before it reaches the registry.

### Step 3 — Decide

Approve the correct ids and reject the rest. `decideSeeds` returns the number of rows updated.

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation{decideSeeds(approveIds:[\"aaaaaaaa-0001\"],rejectIds:[\"aaaaaaaa-0002\"])}"}'
```

Result: `{"data":{"decideSeeds":2}}`.

### Step 4 — Seed the approved, then canonicalize

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){seedApproved(documentId:$id){id status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"seedApproved":{"id":"77777777-...","status":"queued"}}}`. Poll to `succeeded`, then:

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){canonicalizeDocument(documentId:$id){id status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"canonicalizeDocument":{"id":"88888888-...","status":"queued"}}}`. Poll to `succeeded`.

## The same over MCP

When `DOCRAY_MCP_ENABLE_WRITE` is set, run `suggest_seeds`, `decide_seeds`, `seed_approved`, and `canonicalize_document` in the same order. Each reprocessing tool returns a Job to poll with `get_job`; `decide_seeds` returns the number of rows updated. Review the pending suggestions through the seeding screen or the `seedSuggestions` GraphQL query to obtain the ids `decide_seeds` needs.

## Verification

- After canonicalization, entity profiles for the Document show `resolved: true` with a `canonicalName` for the seeded identities. Re-run [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about).
- Approved suggestions move from `pending` to `seeded`; seeded rows are immutable and re-deciding them is a no-op.

## Common failures

- **Canonicalization links nothing** — the registry was empty for those entities; seed first, then canonicalize. This is the primary reason to run the steps in order.
- **A wrong identity was approved** — the candidate resolved to an unrelated real-world entity. Read the `description` before approving; reject mismatches.
- **Write tools absent on MCP** — `DOCRAY_MCP_ENABLE_WRITE` is not set. See [MCP write tools unavailable](/docs/troubleshooting/mcp-write-disabled).
- **Not permitted** — missing `write` scope or document write grant. See [Not permitted](/docs/troubleshooting/not-permitted).

## Next steps

- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about)
- [Organize with lenses](/docs/how-to/organize-with-lenses)
- Concept: [Canonical Entity](/docs/concepts/entities-and-canonicalization)
- Reference: [GraphQL API](/docs/reference/api), [MCP server](/docs/reference/mcp)
