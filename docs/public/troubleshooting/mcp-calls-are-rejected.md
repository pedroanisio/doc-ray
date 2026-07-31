---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "MCP calls are rejected"
doc_type: "troubleshooting"
audience: ["integrator", "administrator"]
surfaces: ["mcp", "config"]
classification: "public"
applies_to: "1.57.1"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# MCP calls are rejected

An AI agent connected to the MCP server has its calls rejected, cannot see the
documents you expect, or cannot find a tool it needs.

## Likely cause

One of four conditions applies:

- **The token is missing or not accepted.** The agent presents no token, or a token
  the deployment does not recognize.
- **The corpus looks empty or wrong.** MCP is owner-scoped: an agent sees exactly
  the corpus of the principal whose token it presents, and nothing else.
- **The public host is not on the allow-list.** Behind a public domain, the MCP
  server accepts only the hosts it is told to accept. A call to any other host is
  rejected before it runs.
- **A write tool is not available.** The MCP server is read-only by default. Write
  tools are registered only when an administrator enables the write gate.

## Resolution

1. Confirm the agent presents a valid token for the intended principal. The corpus
   the agent sees is the corpus that token owns.
   See [How-To: Obtain an API token](../how-to/obtain-an-api-token.md).
2. If the agent sees the wrong corpus, present the token for the principal that
   owns the documents. There is no shared, corpus-wide view.
3. If every call is rejected behind a public domain, an Administrator sets the
   public host on the deployment so the server accepts it. Set
   `DOCRAY_MCP_ALLOWED_HOSTS` (and `DOCRAY_PUBLIC_BASE_URL`) to the public host the
   agent connects to; without this, every call to that host is rejected.
   See [Reference: Configuration](../reference/config.md).
4. If a write tool is missing, expect that: the default MCP surface is read-only.
   To register write tools, an Administrator sets `DOCRAY_MCP_ENABLE_WRITE`. The
   destructive document actions — deleting, archiving, backing up, and restoring —
   are intentionally never available over MCP, in any configuration.

## Confirm it is resolved

The agent lists documents and reads passages for its principal. Where the write
gate is enabled, the expected write tools appear in the agent's tool list and
succeed for documents that principal owns.

## Escalation boundary

Enabling the write gate and setting the allowed hosts are Administrator
configuration tasks. Owner-scoping and the absence of destructive document verbs on
MCP are product limits by design, not errors. If a correctly scoped token with the
correct host still cannot read a document its principal owns, that is a support
matter.

---

Related: [Reference: MCP server](../reference/mcp.md) ·
[Reference: Configuration](../reference/config.md) ·
[FAQ](../faq/index.md#can-an-ai-agent-change-my-data-over-mcp) ·
[Troubleshooting](index.md)
