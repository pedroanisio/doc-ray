---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Not found"
doc_type: "troubleshooting"
audience: ["end-user", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.4"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Not found

## Symptom

A read returns no object for an id that appears well formed.

## Cause

The object may not exist, may be archived or deleted from default listings, or
may be outside the authenticated principal's owner/share/tenant visibility.
Doc-Ray does not reveal hidden objects merely to distinguish those cases.

## Resolution

1. Confirm the id was copied without modification and belongs to the requested
   object type.
2. Record which token and principal made the request; do not print the token.
3. Check the caller's ownership, explicit shares, role, tenant, and lifecycle
   filters.
4. Retry with the correct authorized principal or restore the object when its
   lifecycle state permits it.

Related: [Access and roles](../concepts/access-and-roles.md) ·
[Document lifecycle](../concepts/document-lifecycle.md) ·
[A document link is not found](document-not-found.md)
