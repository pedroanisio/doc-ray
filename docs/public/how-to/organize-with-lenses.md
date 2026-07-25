---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Organize with lenses"
doc_type: "how-to"
audience: ["end-user", "developer", "integrator"]
surfaces: ["api", "mcp"]
classification: "public"
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Organize with lenses

Promote a Collection to a Lens — a named entity-resolution scope — and set a Document's default Lens, naming the identity world its entities belong to.

> **Warning — metadata only.** A Lens is currently metadata only. Naming a scope does **not** change how entities resolve today; it is the foundation for future scoped resolution. Promoting a Collection to a Lens and setting a Document's default Lens change no query result now.

## Prerequisites

- A Collection you own, and a write grant on any Document whose default Lens you set.
- **GraphQL API:** a bearer token with the `write` scope.
- **MCP:** `promote_collection_to_lens`, `demote_lens`, and `set_document_default_lens` are registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`.
- Promoting is a curation decision, not a mechanical step: mark a Collection a Lens only when its Documents share one identity world (a series, a franchise, one work).
- The Global lens cannot be renamed, demoted, or deleted.

## Promote a Collection to a Lens over MCP

```
promote_collection_to_lens  collection_id="99999999-9999-9999-9999-999999999999"
```

Result:

```json
{"id":"99999999-9999-9999-9999-999999999999","name":"Field Notes","kind":"manual","isScope":true,"parentLensId":null,"documentCount":12}
```

`isScope: true` marks the Collection as a Lens. Optionally nest it under an existing Lens by passing `parent_lens_id`.

## Set a Document's default Lens over MCP

The Lens must be a scope Collection the Document already belongs to.

```
set_document_default_lens  document_id="22222222-2222-2222-2222-222222222222"  lens_id="99999999-9999-9999-9999-999999999999"
```

Result:

```json
{"id":"22222222-2222-2222-2222-222222222222","title":"Field Notes","defaultLensId":"99999999-9999-9999-9999-999999999999"}
```

Pass a null `lens_id` to clear the default.

## Demote a Lens over MCP

```
demote_lens  collection_id="99999999-9999-9999-9999-999999999999"
```

Result: the Collection with `isScope` cleared. Demotion is refused for the Global lens, while any Document still names the Lens as its default, and while it still parents another Lens — reassign or demote those first. The Collection and its membership are untouched.

## The same over the GraphQL API

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($c:ID!){promoteCollectionToLens(collectionId:$c){id name isScope}}","variables":{"c":"99999999-9999-9999-9999-999999999999"}}'
```

Result: `{"data":{"promoteCollectionToLens":{"id":"99999999-...","name":"Field Notes","isScope":true}}}`. Use `setDocumentDefaultLens` and `demoteLens` with the same argument names.

## Verification

- List the Lenses and confirm the promoted Collection appears with `isScope: true`:

  ```
  list_lenses  limit=50
  ```

- Fetch the Document and confirm its `defaultLensId` matches the Lens you set.

## Common failures

- **Setting a default Lens is rejected** — the Document does not belong to that scope Collection. Add it to the Collection first with [Manage collections](/docs/how-to/manage-collections).
- **Demotion is refused** — the Lens is the Global lens, is still a default for some Document, or still parents another Lens. Reassign or demote the dependents first.
- **Write tools absent on MCP** — `DOCRAY_MCP_ENABLE_WRITE` is not set. See [MCP write tools unavailable](/docs/troubleshooting/mcp-write-disabled).
- **No visible change to resolution** — expected: a Lens is metadata only today.

## Next steps

- [Manage collections](/docs/how-to/manage-collections)
- [Seed canonical entities](/docs/how-to/seed-canonical-entities)
- Concept: [Lens](/docs/concepts/lenses), [Global lens](/docs/concepts/lenses)
- Reference: [GraphQL API](/docs/reference/api), [MCP server](/docs/reference/mcp)
