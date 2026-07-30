---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Not permitted"
doc_type: "troubleshooting"
audience: ["end-user", "administrator", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Not permitted

## Symptom

A call is authenticated but rejected as forbidden, unauthorized, or outside the
caller's permission.

## Resolution

1. Confirm the request carries the intended bearer or API token without logging
   its value.
2. For a write, confirm the token has `write` scope. For MCP, also confirm the
   deployment has enabled the write-tool gate.
3. Confirm the principal owns the Document, has the required explicit share, or
   holds the Administrator authority required by the operation.
4. Confirm the principal and object belong to the expected tenant.
5. Retry with the least-privileged principal that satisfies those conditions.

Changing an object's visibility or granting access is a durable policy action;
do not broaden access merely to make one request pass.

Related: [Access and roles](../concepts/access-and-roles.md) ·
[Obtain an API token](../how-to/obtain-an-api-token.md) ·
[MCP write tools unavailable](mcp-write-disabled.md)
