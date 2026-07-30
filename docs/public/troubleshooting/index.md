---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Troubleshooting"
doc_type: "troubleshooting"
audience: ["end-user", "administrator", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Troubleshooting

Find the symptom that matches what you see, then follow its entry. Each entry names
the likely cause in plain terms, gives resolution steps, shows how to confirm the
problem is resolved, and states when the issue is a product limit rather than
something you can fix.

## Symptoms

- **A cursor is rejected by another tool.** Cursors are scoped to the list that
  created them. See [A cursor is not interchangeable](cursor-not-interchangeable.md).
- **A background Job failed.** Inspect the Job error and retry only after fixing
  its named cause. See [A Job failed](job-failed.md).
- **MCP rejects the public hostname.** The allowed-host configuration does not
  include the request host. See [MCP host rejected](mcp-host-rejected.md).
- **MCP write tools are absent.** The deployment is intentionally read-only or
  its write gate is disabled. See [MCP write tools unavailable](mcp-write-disabled.md).
- **A Collection name is already used.** Choose a distinct name or update the
  existing Collection. See [Name already in use](name-in-use.md).
- **A read returns no object for a valid-looking id.** The object is absent or
  outside the caller's visibility. See [Not found](not-found.md).
- **A call is not permitted.** Authentication, scope, ownership, sharing, or
  role requirements were not met. See [Not permitted](not-permitted.md).
- **Restore refuses a schema-version mismatch.** Align the target schema with
  the archive before restoring. See [Restore version mismatch](restore-version-mismatch.md).
- **A bounded search says results were truncated.** Narrow the query or continue
  through the documented cursor. See [Result truncated](result-truncated.md).
- **An upload format is rejected.** Use a registered document extension. See
  [Unsupported document type](unsupported-document-type.md).
- **An upload is too large.** Reduce the file or change the administrator-set
  upload ceiling. See [Upload too large](upload-too-large.md).

- **A document link opens to "not found."** The document was deleted and hidden
  from the corpus. See
  [A document link is not found](document-not-found.md).
- **A search returns far fewer results than expected.** A common term flooded one
  region of the document. See
  [Search returns too few results](search-returns-too-few-results.md).
- **An AI agent's MCP calls are rejected.** The token, its scope, the host
  allow-list, or the write gate is the cause. See
  [MCP calls are rejected](mcp-calls-are-rejected.md).
- **An entity's type is wrong or inconsistent.** The type is contested across
  mentions and can be corrected. See
  [Entity types look wrong](entity-types-look-wrong.md).
- **Canonicalization links almost nothing.** The registry has no entities to link
  to yet. See
  [Canonicalization links nothing](canonicalization-links-nothing.md).

---

Related: [FAQ](../faq/index.md) · [Documentation home](../index.md)
