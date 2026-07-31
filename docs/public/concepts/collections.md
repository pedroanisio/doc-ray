---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Collections"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Collections

This page defines a Collection and the difference between a manual Collection you curate by hand and a smart Collection defined by a rule.

## Definition

A **Collection** is a named, browsable group of Documents within your Corpus. Collections come in two kinds:

- **Manual Collection** — a hand-curated set. Add and remove Documents explicitly; membership is exactly what you put in it.
- **Smart Collection** — a rule-driven set. Instead of listing members, a smart Collection carries a rule, and every Document that matches the rule belongs to it automatically. The rule matches on a Document's type, its document status, and its tags. As Documents that match are ingested or change, the Collection's membership follows.

A smart Collection's rule is summarized in plain terms, for example `status: extracted; tags: api`, so its criteria are legible at a glance.

## Why it exists and when it applies

A manual Collection fits a curated reading set, a project bundle, or any group whose membership is a deliberate choice. A smart Collection fits a standing view — "every extracted Document tagged `api`" — that should stay current without manual upkeep.

Prefer a Collection over ad-hoc tags when the intent is a named, browsable group rather than a loose label. Tags describe a Document; a Collection gathers Documents.

## How it relates to neighboring concepts

- **Corpus and documents** — a Collection groups Documents that are already in your Corpus; it never widens visibility. You see only members you are already permitted to see.
- **Lenses** — a Collection can be promoted to a Lens to name a scope. Promotion is a separate action; a Collection is a group, a Lens is a named scope.
- **Document status and tags** — a smart Collection's rule reads a Document's type, status, and tags to decide membership.

## Limitations

- A smart Collection's membership is defined by its rule. Documents cannot be added to or removed from it directly; adding to a rule-driven Collection is rejected. Change its membership by changing the rule or by changing the Documents that match.
- A smart Collection's rule matches only on document type, document status, and tags. No other criteria participate.
- A Collection never grants access. A Document you cannot see does not appear in a Collection, even when it matches a smart rule.
- Deleting a Collection removes the grouping, not the Documents in it.

## Related how-tos and reference

- How-to: [Create a collection](../how-to/manage-collections.md)
- How-to: [Promote a collection to a lens](../how-to/organize-with-lenses.md)
- Concept: [Corpus and documents](./corpus-and-documents.md)
- Concept: [Lenses](./lenses.md)
- Reference: [GraphQL API](../reference/api.md)
- Reference: [MCP server](../reference/mcp.md)
