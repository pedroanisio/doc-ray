---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "A Job failed"
doc_type: "troubleshooting"
audience: ["end-user", "administrator", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# A Job failed

## Symptom

A write returned a Job, but polling later shows `status: failed` and an `error`.

## Resolution

1. Read the Job's `kind`, `documentId`, `error`, timing, attempts, and progress
   fields through the web interface, `job`/`jobs`, or `get_job`.
2. Fix the named cause before enqueueing the operation again. Common causes are
   an unsupported input, an unavailable model or service, invalid configuration,
   or a permission failure.
3. Re-run the relevant operation for that Document. Do not treat the original
   enqueue response as completion evidence.
4. Poll the new Job to a terminal status, then read the expected downstream
   artifact to confirm it exists.

If the error does not identify a recoverable operator action, preserve the Job
id and error text for diagnosis rather than retrying indefinitely.

Related: [Jobs](../concepts/jobs.md) · [GraphQL API](../reference/api.md) ·
[MCP server](../reference/mcp.md)
