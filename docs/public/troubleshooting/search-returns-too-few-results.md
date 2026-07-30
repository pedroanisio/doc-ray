---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Search returns too few results"
doc_type: "troubleshooting"
audience: ["end-user", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Search returns too few results

A search returns far fewer passages than expected, or seems to miss sections of a
document you know contain the term.

## Likely cause

A search response is a set of cited passages, not the whole document. When a query
term is common, it appears many times in one region and crowds out passages from
the rest of the document, so the result looks sparse even though the content is
present. Every result reports this: a `coverage` field and a `note` say when a
common term flooded one region. In `match="phrase"`, the same fields also say
when boundary verification scanned its maximum 2,001 candidates before filling
the hit page. A sparse result is usually a query problem, not missing content.

## Resolution

1. Read the result's `coverage` and `note`. When they report that a term flooded
   one region, the missing sections were crowded out, not absent.
   For phrase mode, inspect `candidate_limit_reached`; if true, the verified
   result is bounded rather than exhaustive even when it contains no hits.
2. Search again with rarer, more specific terms. Prefer a distinctive name, phrase,
   figure, or quotation over a frequent, general word.
3. Add a second specific term to narrow the query to the passage you want.
4. To cover a document completely rather than search it, read it by section from
   its outline instead of relying on a keyword query.
   See [How-To: Read a document](../how-to/read-a-document.md).

## Confirm it is resolved

Run the refined query. The `coverage` field no longer reports flooding, and the
result includes passages from the sections you expected.

## Escalation boundary

Coverage flooding is an expected property of keyword search over long documents and
is signalled in every result — it is a query technique matter, not a defect. If a
distinctive, rare term that appears verbatim in a document you can access returns
nothing at all, and `coverage` reports no flooding, that is a support matter.

---

Related: [Concept: Corpus and documents](../concepts/corpus-and-documents.md) ·
[How-To: Search the corpus](../how-to/search-the-corpus.md) ·
[FAQ](../faq/index.md#why-did-my-search-return-so-few-results) ·
[Troubleshooting](index.md)
