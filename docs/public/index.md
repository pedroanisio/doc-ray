---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Doc-Ray documentation"
doc_type: "concept"
audience: ["end-user", "administrator", "developer", "integrator", "operator", "evaluator"]
surfaces: ["webapp", "api", "mcp", "cli", "config"]
classification: "public"
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Doc-Ray documentation

Doc-Ray is a living library and knowledge base: it ingests documents into a
queryable, linguistically annotated corpus, resolves the people, places, dates, and
organizations inside them into shared canonical identities, and exposes the result
to people, applications, and AI agents.

This documentation applies to version 1.54.0.

## Sections

- **[FAQ](faq/index.md)** — short, direct answers to common questions.
- **[How-To guides](how-to/index.md)** — steps to accomplish a specific task.
- **[Concepts](concepts/index.md)** — what each part of the product is and why it
  works the way it does.
- **[Reference](reference/index.md)** — the exact contract of each surface: GraphQL
  API, MCP server, Operator CLI, and Configuration.
- **[Release notes](release-notes/index.md)** — changes, operator actions, and
  migration guidance by version.
- **[Troubleshooting](troubleshooting/index.md)** — what a symptom means and how to
  resolve it.

## Start here, by role

### New users

Understand the model, then ingest and explore a corpus.

- [What Doc-Ray is](faq/index.md#what-is-doc-ray)
- [Concept: Corpus](concepts/corpus-and-documents.md)
- [How-To: Ingest a document](how-to/ingest-a-document.md)
- [How-To: Search the corpus](how-to/search-the-corpus.md)
- [Concept: Document lifecycle](concepts/document-lifecycle.md) — how archive,
  delete, and purge relate.

### Administrators

Run an instance: access, tokens, and backups.

- [How-To: Obtain an API token](how-to/obtain-an-api-token.md)
- [Reference: Configuration](reference/config.md)
- [Reference: Operator CLI](reference/cli.md)
- [Concept: Backups](concepts/backups.md) and
  [How-To: Create a backup](how-to/back-up-and-restore.md)
- [Concept: Access and sharing](concepts/access-and-roles.md)

### Developers

Integrate against the GraphQL API.

- [Reference: GraphQL API](reference/api.md)
- [Concept: Jobs](concepts/jobs.md) — every write enqueues a job; poll it.
- [Concept: Canonical entities](concepts/entities-and-canonicalization.md)
- [How-To: Search the corpus](how-to/search-the-corpus.md)

### Integrators

Connect an AI agent through the MCP server, safely.

- [Reference: MCP server](reference/mcp.md) — read-only by default; owner-scoped.
- [Reference: Configuration](reference/config.md) — enable the write gate with
  `DOCRAY_MCP_ENABLE_WRITE`.
- [How-To: Connect an agent over MCP](how-to/connect-an-agent-over-mcp.md)
- [Troubleshooting: MCP calls are rejected](troubleshooting/mcp-calls-are-rejected.md)

---

This documentation inherits the epistemic commitments in
[DISCLAIMER.md](../../DISCLAIMER.md).
