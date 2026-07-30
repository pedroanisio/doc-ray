---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "MCP write tools unavailable"
doc_type: "troubleshooting"
audience: ["administrator", "integrator"]
surfaces: ["mcp", "config"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# MCP write tools unavailable

## Symptom

Read tools are present, but expected curation or reprocessing tools do not
appear in the MCP tool list.

## Cause

MCP is read-only by default. Write tools are not registered unless the
deployment sets `DOCRAY_MCP_ENABLE_WRITE`.

## Resolution

1. Decide whether this client and deployment should expose write tools.
2. If approved, set `DOCRAY_MCP_ENABLE_WRITE=true` in the deployment environment.
3. Recreate the MCP container.
4. Re-list tools and authenticate with a token that also has `write` scope.

The environment gate controls whether tools exist; token scope and Document
permissions still control whether an offered write succeeds.

Related: [MCP server](../reference/mcp.md) ·
[Connect an agent over MCP](../how-to/connect-an-agent-over-mcp.md) ·
[Not permitted](not-permitted.md)
