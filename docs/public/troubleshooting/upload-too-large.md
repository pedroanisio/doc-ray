---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Upload too large"
doc_type: "troubleshooting"
audience: ["end-user", "administrator", "integrator"]
surfaces: ["webapp", "api", "config"]
classification: "public"
applies_to: "1.54.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Upload too large

## Symptom

The web interface or GraphQL upload rejects a file because its request body
exceeds the deployment ceiling.

## Cause

`DOCRAY_MAX_UPLOAD_MB` bounds uploads before the service buffers or stores an
oversized body. The default is 200 MB unless the administrator configures a
different limit.

## Resolution

1. Confirm the source file size and the deployment's configured ceiling.
2. Remove unintended embedded assets or produce a smaller faithful source copy.
3. If the corpus legitimately requires larger files, an administrator may raise
   `DOCRAY_MAX_UPLOAD_MB` after reviewing memory, proxy, and storage limits, then
   recreate the affected services.
4. Retry and poll the returned ingest Job to completion.

Related: [Ingest a document](../how-to/ingest-a-document.md) ·
[Configuration](../reference/config.md)
