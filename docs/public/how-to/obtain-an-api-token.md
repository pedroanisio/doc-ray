---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Obtain an API token"
doc_type: "how-to"
audience: ["administrator", "integrator"]
surfaces: ["cli", "api", "mcp"]
classification: "public"
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Obtain an API token

Mint a bearer token that maps to a principal, so a person, application, or AI
agent can authenticate to the GraphQL API and the MCP server.

## Prerequisites

- **Role:** Administrator. Tokens are provisioned with the Operator CLI, which
  requires operator access to the deployment.
- A **principal** for the token to represent. Create one with
  `python -m doc_ray.entrypoints.auth_admin principal` if it does not exist yet.
- The database connection string available to the CLI (the `DATABASE_URL`
  environment variable, or the `--database-url` flag).

> A token grants every permission of the principal it maps to. Treat it as a
> secret: transmit it over TLS only, store it in a secret manager, and revoke it
> by removing its mapping if it is exposed.

## Mint a token for the API

1. Mint a token bound to a principal, with the scopes it should carry:

   ```bash
   python -m doc_ray.entrypoints.auth_admin token mint \
     --principal <PRINCIPAL_ID> \
     --scopes read+write
   ```

   **Expected result:** the command prints a `DOCRAY_API_TOKENS` mapping of the
   form `<TOKEN>=<PRINCIPAL_ID>:read+write`.

2. Register the mapping with the API by setting it in the deployment's
   configuration:

   ```
   DOCRAY_API_TOKENS=<TOKEN>=<PRINCIPAL_ID>:read+write
   ```

   To append it directly to a `.env` file instead of copying it by hand, pass
   `--env-file <PATH>`; add `--fail-closed` to also set
   `DOCRAY_API_ALLOW_ANONYMOUS=false`, which requires a token on every request.

   **Expected result:** after the API reloads its configuration, requests
   presenting the token resolve to the principal and are scoped to what that
   principal may read and write.

## Mint a token for an MCP client

For a token intended for an AI agent connecting over MCP, use the MCP-specific
command, which mints a token suited to a connector:

```bash
python -m doc_ray.entrypoints.auth_admin mcp-token mint --principal <PRINCIPAL_ID>
```

**Expected result:** a token mapping the agent presents as a bearer credential to
the MCP server. The agent sees only the corpus that principal may read, and its
tools are read-only unless the deployment has enabled MCP write tools. See
[Connect an agent over MCP](connect-an-agent-over-mcp.md).

## Present the token

Send the token as a bearer credential on every request:

```
Authorization: Bearer <TOKEN>
```

The GraphQL API also accepts the token in an `X-DocRay-API-Token` header.

**Verification.** Issue any read and confirm it resolves to the intended
principal — for example, request `version` from the GraphQL API, or call
`list_documents` over MCP and confirm it returns that principal's documents and
no others.

## Common failures

- **Requests are rejected as unauthenticated** after you set
  `DOCRAY_API_ALLOW_ANONYMOUS=false`: the token mapping is missing or malformed.
  Confirm `DOCRAY_API_TOKENS` contains the exact `<TOKEN>=<PRINCIPAL_ID>:<scopes>`
  entry and that the API reloaded its configuration.
- **An MCP client authenticates but every call is rejected:** the deployment's
  public host is not allow-listed. See
  [MCP calls are rejected](../troubleshooting/mcp-calls-are-rejected.md).

## Next steps

- [Connect an agent over MCP](connect-an-agent-over-mcp.md)
- Configuration reference: [access and authentication](../reference/config.md)
- Concept: [Access and roles](../concepts/access-and-roles.md)
