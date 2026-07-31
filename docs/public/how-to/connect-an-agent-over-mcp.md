---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Connect an agent over MCP"
doc_type: "how-to"
audience: ["integrator", "administrator"]
surfaces: ["mcp", "config"]
classification: "restricted"
applies_to: "1.57.1"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Connect an agent over MCP

Point an MCP client at the Doc-Ray MCP server, authenticate it with a bearer token, and understand its read-only default, owner-scoping, and how an administrator enables write tools.

## Prerequisites

- The MCP server endpoint: `/mcp` at the deployment's public base URL.
- A **bearer token** for the principal the agent will act as. An administrator provisions tokens; `DOCRAY_API_TOKENS` maps a bearer token to a principal and its scopes (`token=uuid[:read+write]`).
- For a deployment behind a public domain, the administrator must set `DOCRAY_MCP_ALLOWED_HOSTS` and/or `DOCRAY_PUBLIC_BASE_URL`; otherwise the server rejects every call.
- The MCP surface is **read-only by default**. Write tools exist only when the administrator sets `DOCRAY_MCP_ENABLE_WRITE`.

## Owner-scoping

An agent sees exactly the Corpus of the principal whose token it presents. Every tool result is scoped to what that principal may read — the MCP server grants nothing beyond it. Give an agent a token for a principal whose visibility matches the work; do not present an administrator token to a task that should see only one user's Documents.

## Step 1 — Point the client at the endpoint

Configure the MCP client with the server URL and the bearer token. The token is sent as an `Authorization: Bearer` header on every call.

```json
{
  "mcpServers": {
    "doc-ray": {
      "url": "https://doc-ray.example/mcp",
      "headers": { "Authorization": "Bearer REDACTED_TOKEN" }
    }
  }
}
```

Result: on connect, the client lists the server's tools. With the default configuration, only read tools are present (for example `search_sentences`, `read_document_outline`, `read_document_sentences`, `full_text_search`, `list_documents`, `get_document`, `entity_profiles`, `get_job`).

## Step 2 — Confirm the connection and scope

Call a read tool to confirm authentication and see the principal's Corpus:

```
list_documents  limit=5
```

Result: up to five Documents visible to the token's principal, newest first. An empty list means the principal owns no visible Documents; a permission error means the token is missing or invalid.

## Step 3 — Enable write tools (administrator)

Write tools are registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`. With it off, curation and reprocessing tools such as `correct_entity_mention`, `update_document_metadata`, `create_collection`, and `promote_collection_to_lens` are absent.

- Set `DOCRAY_MCP_ENABLE_WRITE` in Configuration and restart the MCP server to register the write tools.

  Result: after reconnecting, the client's tool list includes the reprocessing and curation write tools. Destructive document verbs — delete, archive, backup, restore, cancel job — remain absent from MCP by design.

Even with write tools enabled, the token must carry the `write` scope, and each document-scoped write still requires a write grant on the target Document.

## Verification

- The client lists the expected tools: read-only tools with the default configuration, and additionally the write tools when `DOCRAY_MCP_ENABLE_WRITE` is set.
- `list_documents` returns only Documents the token's principal may read, confirming owner-scoping.
- A write tool call succeeds only when the token has the `write` scope and a grant on the Document.

## Common failures

- **Every call is rejected behind a public domain** — `DOCRAY_MCP_ALLOWED_HOSTS`/`DOCRAY_PUBLIC_BASE_URL` are unset or do not match the request host. See [MCP host rejected](/docs/troubleshooting/mcp-host-rejected).
- **Write tools are missing** — `DOCRAY_MCP_ENABLE_WRITE` is not set; the surface is read-only. See [MCP write tools unavailable](/docs/troubleshooting/mcp-write-disabled).
- **Permission denied** — the bearer token is missing, invalid, or lacks the needed scope. See [Not permitted](/docs/troubleshooting/not-permitted).
- **The agent sees no Documents** — the token's principal owns nothing visible; owner-scoping is working as intended.

## Next steps

- [Search the corpus](/docs/how-to/search-the-corpus)
- [Read a document](/docs/how-to/read-a-document)
- [Correct an entity](/docs/how-to/correct-an-entity)
- Reference: [MCP server](/docs/reference/mcp), [Configuration](/docs/reference/config)
