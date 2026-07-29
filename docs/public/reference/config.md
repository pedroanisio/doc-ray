---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Configuration reference"
doc_type: "reference"
audience: ["administrator", "operator"]
surfaces: ["config"]
classification: "public"
applies_to: "1.54.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Configuration reference

This page is the contract for the administrator-facing configuration variables: name, default, and effect. Each is set as an environment variable in the deployment environment.

Only the variables below are publicly supported for direct use. Doc-Ray reads roughly 140 additional variables; those are Internal, may change without notice, and are not documented here — do not set them directly.

## Access and authentication

### `DOCRAY_API_ALLOW_ANONYMOUS`

- **Default:** `true`
- **Effect:** Allows unauthenticated API access, scoped to an anonymous principal by the same access rules as any principal. Set to `false` to require a token on every request.

### `DOCRAY_API_TOKENS`

- **Default:** unset (no static tokens)
- **Effect:** Maps bearer tokens to principals. Each entry has the form `token=uuid[:read+write]`; the optional scope suffix grants write in addition to read. A request presenting a listed token is authenticated as its principal. Mint entries with the Operator CLI (`auth_admin token mint`). Treat every token as a secret.

### `DOCRAY_AUTH_REGISTRATION_ENABLED`

- **Default:** `false`
- **Effect:** Enables self-service local registration. Leave disabled to provision principals only through the Operator CLI.

## MCP

### `DOCRAY_MCP_ENABLE_WRITE`

- **Default:** off (unset)
- **Effect:** Registers the MCP write tools. When unset, the MCP surface is read-only and the write tools are absent from the tool list. See [MCP server](./mcp.md).

### `DOCRAY_MCP_ALLOWED_HOSTS`

- **Default:** unset
- **Effect:** The public host or hosts the MCP server accepts. Behind a public domain, a call whose host is not listed here (or covered by `DOCRAY_PUBLIC_BASE_URL`) is rejected.

### `DOCRAY_PUBLIC_BASE_URL`

- **Default:** unset
- **Effect:** The public base URL the deployment is served from. It contributes the accepted host for MCP requests; required behind a public domain, or every MCP call is rejected. See [MCP calls are rejected](../troubleshooting/mcp-calls-are-rejected.md).

## Backups

### `DOCRAY_BACKUP_ENABLED`

- **Default:** `false`
- **Effect:** Master switch for the in-app backup subsystem. When false, the backup scheduler exits without enqueueing anything, `createBackup` / `restoreBackup` are refused, the archive download route and the `/backups` pages return *Not Found*, the Backups navigation entry is hidden in both interfaces, and a worker that picks up a Backup queued earlier cancels it instead of running it. The three settings below have no effect while this is false.
- **Note:** A managed database provider's own backups cover the database only, not the object store holding original uploaded files. See [Backups](../concepts/backups.md).

### `DOCRAY_BACKUP_SCHEDULE_SECONDS`

- **Default:** `86400`
- **Effect:** The cadence, in seconds, at which the backup scheduler enqueues an automatic Backup. Requires `DOCRAY_BACKUP_ENABLED`.

### `DOCRAY_BACKUP_RETENTION_COUNT`

- **Default:** `7`
- **Effect:** The number of newest Backup archives always kept, regardless of age.

### `DOCRAY_BACKUP_RETENTION_DAYS`

- **Default:** `30`
- **Effect:** Backup archives older than this are pruned once they fall outside the newest kept by `DOCRAY_BACKUP_RETENTION_COUNT`.

See [Backups](../concepts/backups.md) and the `backup_scheduler` command in [Operator CLI](./cli.md).

## Email

### `DOCRAY_SMTP_HOST`

- **Default:** unset
- **Effect:** The outbound SMTP host used for email such as verification and invites. Without it, email is not sent.

### `DOCRAY_SMTP_PORT`

- **Default:** unset
- **Effect:** The outbound SMTP port used with `DOCRAY_SMTP_HOST`.

## Related concepts, how-tos, and reference

- Concept: [Access and roles](../concepts/access-and-roles.md)
- Concept: [Backups](../concepts/backups.md)
- How-to: [Obtain an API token](../how-to/obtain-an-api-token.md)
- Reference: [Operator CLI](./cli.md)
- Reference: [GraphQL API](./api.md)
- Reference: [MCP server](./mcp.md)
- Troubleshooting: [MCP calls are rejected](../troubleshooting/mcp-calls-are-rejected.md)
</content>
