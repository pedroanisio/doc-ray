---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Name already in use"
doc_type: "troubleshooting"
audience: ["end-user", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Name already in use

## Symptom

Creating or renaming a Collection fails because its name already exists.

## Cause

Collection names are unique. The requested name identifies an existing
Collection, including one that may already be the intended target.

## Resolution

1. List visible Collections and check the existing name, id, kind, and members.
2. Reuse or update that Collection if it represents the same grouping.
3. Otherwise choose a distinct, descriptive name and retry.
4. Confirm the returned Collection id and use that id for later membership or
   Lens operations.

Related: [Collections](../concepts/collections.md) ·
[Manage Collections](../how-to/manage-collections.md)
