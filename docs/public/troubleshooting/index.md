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
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Troubleshooting

Find the symptom that matches what you see, then follow its entry. Each entry names
the likely cause in plain terms, gives resolution steps, shows how to confirm the
problem is resolved, and states when the issue is a product limit rather than
something you can fix.

## Symptoms

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
