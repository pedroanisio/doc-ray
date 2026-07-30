---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Edit document metadata"
doc_type: "how-to"
audience: ["end-user", "developer", "integrator"]
surfaces: ["api", "mcp"]
classification: "public"
applies_to: "1.57.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Edit document metadata

Edit a Document's title, description, tags, or authors, and read the change history that records every edit.

## Prerequisites

- A write grant on the Document, and its id.
- **GraphQL API:** a bearer token with the `write` scope.
- **MCP:** `update_document_metadata` is registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`.
- Only the fields passed change. `tags` (a set) and `authors` (an ordered list — author order is kept) each fully replace the current set when passed; pass `authors: []` to clear them. Each changed field is recorded in the change history.

## Edit over MCP

Editing enqueues a Job. Poll it with `get_job`.

1. Change the title and tags:

   ```
   update_document_metadata  document_id="22222222-2222-2222-2222-222222222222"  title="Field Notes, Vol. II"  tags=["survey","2026"]
   ```

   Result:

   ```json
   {"id":"aaaa1111-2222-3333-4444-555566667777","kind":"update_metadata","status":"queued","documentId":"22222222-2222-2222-2222-222222222222","error":null}
   ```

2. Poll the Job to `succeeded`:

   ```
   get_job  job_id="aaaa1111-2222-3333-4444-555566667777"
   ```

   Result: `{"id":"aaaa1111-...","status":"succeeded","error":null}`.

3. Set authors in the order they should appear:

   ```
   update_document_metadata  document_id="22222222-2222-2222-2222-222222222222"  authors=["A. Researcher","B. Analyst"]
   ```

   Result: a queued Job; once succeeded, the Document's authors are exactly that ordered list.

## Edit over the GraphQL API

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"mutation($id:ID!){updateDocumentMetadata(documentId:$id,title:\"Field Notes, Vol. II\",tags:[\"survey\",\"2026\"]){id kind status}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result: `{"data":{"updateDocumentMetadata":{"id":"aaaa1111-...","kind":"update_metadata","status":"queued"}}}`. Poll the Job to `succeeded`.

## Read the change history

```bash
curl -s https://doc-ray.example/graphql \
  -H "Authorization: Bearer REDACTED_TOKEN" -H "content-type: application/json" \
  -d '{"query":"query($id:ID!){documentMetadataHistory(documentId:$id){field oldValue newValue changedAt actor}}","variables":{"id":"22222222-2222-2222-2222-222222222222"}}'
```

Result (oldest first):

```json
{"data":{"documentMetadataHistory":[
  {"field":"title","oldValue":"Field Notes","newValue":"Field Notes, Vol. II","changedAt":"2026-07-25T14:20:00Z","actor":"REDACTED_PRINCIPAL"},
  {"field":"tags","oldValue":"survey","newValue":"survey, 2026","changedAt":"2026-07-25T14:20:00Z","actor":"REDACTED_PRINCIPAL"}
]}}
```

Only actually-changed fields appear; a mutation that sets a field to its current value records nothing.

## Verification

- Fetch the Document and confirm the new values:

  ```
  get_document  document_id="22222222-2222-2222-2222-222222222222"
  ```

  The returned `title`, `tags`, and `authors` match the edit, and the change history holds one row per changed field.

## Common failures

- **Not permitted** — missing `write` scope or write grant on the Document. See [Not permitted](/docs/troubleshooting/not-permitted).
- **Tags or authors were replaced, not merged** — passing `tags` or `authors` fully replaces the set. To merge tags across Documents instead, use bulk tagging.
- **Write tool absent on MCP** — `DOCRAY_MCP_ENABLE_WRITE` is not set. See [MCP write tools unavailable](/docs/troubleshooting/mcp-write-disabled).

## Next steps

- [Manage collections](/docs/how-to/manage-collections)
- [Share a document](/docs/how-to/share-a-document)
- Concept: [Document](/docs/concepts/corpus-and-documents)
- Reference: [GraphQL API](/docs/reference/api), [MCP server](/docs/reference/mcp)
