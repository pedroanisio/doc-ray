---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Lenses"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["webapp", "api"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Lenses

This page defines a Lens and the Global lens, and states plainly that a Lens is metadata only today and does not change how entities resolve.

## Definition

A **Lens** is a named scope over your Corpus. It gives a group of Documents an identity you can refer to.

The **Global lens** is the default, always-present scope covering the whole Corpus. It is a fixed reference point, not a Lens you create or manage.

## Why it exists and when it applies

A Lens names a slice of the Corpus so it can be referred to and built upon. It is the foundation for scoped entity resolution in the future — a way to say "resolve entities within this scope" once that capability exists.

Use a Lens today to give a scope a stable name. A Collection can be promoted to a Lens when a curated group should become a named scope.

## How it relates to neighboring concepts

- **Collections** — a Collection is a group of Documents; a Lens is a named scope. Promoting a Collection to a Lens turns the group into a scope.
- **Canonical Entities and entity resolution** — a Lens names a scope in which resolution could one day be run. Today it does not participate in resolution.
- **Corpus and documents** — the Global lens corresponds to the whole Corpus.

## Limitations

- **A Lens is metadata only today.** Naming a scope does **not** change how Entity Mentions resolve to Canonical Entities. Resolution behaves the same whether or not a Lens is defined. The Lens is groundwork for future scoped resolution, not an active setting that alters results now.
- **The Global lens cannot be renamed, demoted, or deleted.** It is fixed.
- A Lens does not change visibility. It names a scope within what you can already see; it does not grant access to Documents outside your Corpus.

## Related how-tos and reference

- How-to: [Promote a collection to a lens](../how-to/organize-with-lenses.md)
- Concept: [Collections](./collections.md)
- Concept: [Entities and canonicalization](./entities-and-canonicalization.md)
- Concept: [Corpus and documents](./corpus-and-documents.md)
- Reference: [GraphQL API](../reference/api.md)
