---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Result truncated"
doc_type: "troubleshooting"
audience: ["end-user", "developer", "integrator"]
surfaces: ["api", "mcp"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Result truncated

## Symptom

A search or frequency result reports a cap, partial coverage, a floor, or fewer
rows than the caller expected.

## Cause

Some high-frequency searches bound candidate work so one common term cannot
consume the whole service. A capped count is a lower bound, not proof that no
other matches exist.

## Resolution

1. Read the result's `coverage`, `capped`, `note`, and measurement fields.
2. Prefer a rarer phrase, add a Document filter, or narrow the requested scope.
3. For ordinary paginated lists, continue with the cursor returned by that same
   list. Do not reuse it in another tool.
4. Use `termFrequency` before a broad Sentence search to identify flood risk.

Related: [Search the corpus](../how-to/search-the-corpus.md) ·
[Search returns too few results](search-returns-too-few-results.md) ·
[A cursor is not interchangeable](cursor-not-interchangeable.md)
