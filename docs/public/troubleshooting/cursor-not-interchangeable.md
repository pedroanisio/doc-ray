---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "A cursor is not interchangeable"
doc_type: "troubleshooting"
audience: ["end-user", "developer", "integrator"]
surfaces: ["api", "mcp"]
classification: "public"
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# A cursor is not interchangeable

## Symptom

A paginated call rejects an `after` cursor that another field or tool returned.

## Cause

Cursors encode the ordering and identity of one list connection. A Sentence
search cursor is not a reader cursor, and neither can page Documents, Jobs, or
another filtered list.

## Resolution

1. Discard the foreign cursor.
2. Repeat the intended field or tool without `after`.
3. Continue only with the last cursor returned by that same field, tool, and
   filter set.
4. For aimed Document reads, use Sentence ordinals or the reader's own cursor.

The issue is resolved when the next page comes from the same connection without
repeating or skipping rows.

Related: [Read a document](../how-to/read-a-document.md) ·
[Search the corpus](../how-to/search-the-corpus.md)
