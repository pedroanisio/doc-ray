---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "MCP host rejected"
doc_type: "troubleshooting"
audience: ["administrator", "integrator"]
surfaces: ["mcp", "config"]
classification: "public"
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# MCP host rejected

## Symptom

The public MCP endpoint returns HTTP 421 or reports an invalid Host header.

## Cause

The MCP server's DNS-rebinding guard does not recognize the hostname used by
the client. The public base URL or explicit allowed-host list is absent or does
not match the request host.

## Resolution

1. Set `DOCRAY_PUBLIC_BASE_URL` to the deployed HTTPS origin, or add the exact
   host to `DOCRAY_MCP_ALLOWED_HOSTS`.
2. Recreate the MCP container so it reads the new environment.
3. Confirm the tunnel or reverse-proxy route sends `/mcp` to the MCP service.
4. Probe the public endpoint again. An unauthenticated 401 or 403 proves the
   service is reachable with authentication enforced; 421 does not.

Related: [Connect an agent over MCP](../how-to/connect-an-agent-over-mcp.md) ·
[Configuration](../reference/config.md) · [MCP server](../reference/mcp.md)
