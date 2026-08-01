---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Reference"
doc_type: "reference"
audience: ["developer", "integrator", "operator", "administrator"]
surfaces: ["api", "mcp", "cli", "config"]
classification: "public"
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Reference

This section is the exact contract of each supported Doc-Ray surface: the fields, tools, commands, and variables it exposes, with their arguments, defaults, and effects.

## Pages

- [GraphQL API](./api.md) — the `/graphql` endpoint: authentication, the write-enqueues-a-Job model, keyset pagination, and every query and mutation field.
- [MCP server](./mcp.md) — the `/mcp` endpoint for AI agents: owner-scoped authentication, the read-only default, the write gate, and every read, composition, and write tool.
- [Operator CLI](./cli.md) — the supported operator commands: provisioning, migrations, scheduled backups, diagnostics, and batch ingestion.
- [Configuration](./config.md) — the administrator-facing environment variables, with default and effect.

## Related sections

- [Concepts](../concepts/corpus-and-documents.md) — what each noun means and why it works the way it does.
- [Troubleshooting](../troubleshooting/index.md) — symptoms, causes, and resolutions.
</content>
