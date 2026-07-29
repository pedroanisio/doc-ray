---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Manage collections"
doc_type: "how-to"
audience: ["end-user", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.54.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Manage collections

Group Documents into named Collections — a hand-curated `manual` set you fill yourself, or a `smart` set defined by a rule that auto-populates — and rename, refill, or delete them.

## Prerequisites

- A signed-in account (Member or higher) with a write grant on the Documents you add.
- **GraphQL API:** a bearer token with the `write` scope.
- **MCP:** the collection write tools are registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`.
- Collection names are globally unique. A `manual` Collection is filled by adding Documents; a `smart` Collection is populated by its rule and rejects manual additions. Deleting a Collection removes only the grouping — its member Documents are untouched.

## Manage through the Corpus browser

1. Open **Collections** (`/collections`).
2. Create a Collection, giving it a unique name and choosing manual or smart.

   Result: the new Collection appears in the list with a member count of zero.

3. Open a Collection (`/collections/<collection-id>`) to see its members.
4. For a manual Collection, add or remove Documents from its detail page.

   Result: the member count changes to match.

## Create a manual Collection over MCP

```
create_collection  name="Field Notes 2026"  description="Survey write-ups"  kind="manual"
```

Result:

```json
{"id":"99999999-9999-9999-9999-999999999999","name":"Field Notes 2026","description":"Survey write-ups","kind":"manual","ruleSummary":null,"documentCount":0,"isScope":false,"parentLensId":null}
```

## Create a smart Collection over MCP

A smart Collection takes a `rule` (JSON) plus optional `doc_type`, `status`, and `tags` filters that auto-populate it:

```
create_collection  name="All PDFs"  kind="smart"  doc_type="pdf"
```

Result: a Collection whose `ruleSummary` describes the filter and whose `documentCount` reflects the matching Documents. Do not add Documents to a smart Collection manually — the rule owns its membership.

## Add and remove Documents over MCP

```
add_document_to_collection  collection_id="99999999-9999-9999-9999-999999999999"  document_id="22222222-2222-2222-2222-222222222222"
```

Result: the updated Collection with `documentCount` incremented. Adding to a smart Collection is rejected.

```
remove_document_from_collection  collection_id="99999999-9999-9999-9999-999999999999"  document_id="22222222-2222-2222-2222-222222222222"
```

Result: the updated Collection with `documentCount` decremented.

## Rename or delete over MCP

```
update_collection  collection_id="99999999-9999-9999-9999-999999999999"  name="Field Notes"
```

Result: the updated Collection carrying the new name. Renaming to a name another Collection already holds is a clean error.

```
delete_collection  collection_id="99999999-9999-9999-9999-999999999999"
```

Result: `true`. The container is removed; its member Documents remain in the Corpus.

## The same over the GraphQL API

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation{createCollection(name:\"Field Notes 2026\",description:\"Survey write-ups\",kind:\"manual\"){id name kind documentCount}}"}'
```

Result: `{"data":{"createCollection":{"id":"99999999-...","name":"Field Notes 2026","kind":"manual","documentCount":0}}}`. Use `addDocumentToCollection`, `removeDocumentFromCollection`, `updateCollection`, and `deleteCollection` with the same argument names.

## Verification

- List Collections and confirm the new name and member count:

  ```
  get_collection  collection_id="99999999-9999-9999-9999-999999999999"
  ```

  The returned `documentCount` and `documents` list reflect every add and remove.

## Common failures

- **Name already exists** — Collection names are globally unique; choose another. See [Name already in use](/docs/troubleshooting/name-in-use).
- **Adding to a smart Collection is rejected** — a rule-driven Collection owns its membership; adjust the rule instead.
- **Not permitted** — missing `write` scope, or no write grant on the Document being added. See [Not permitted](/docs/troubleshooting/not-permitted).
- **Write tools absent on MCP** — `DOCRAY_MCP_ENABLE_WRITE` is not set. See [MCP write tools unavailable](/docs/troubleshooting/mcp-write-disabled).

## Next steps

- [Organize with lenses](/docs/how-to/organize-with-lenses)
- Concept: [Collection](/docs/concepts/collections)
- Reference: [GraphQL API](/docs/reference/api), [MCP server](/docs/reference/mcp)
