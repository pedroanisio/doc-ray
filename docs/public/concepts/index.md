---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Concepts"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["webapp", "api", "mcp", "cli", "config"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Concepts

This section defines the nouns a reader must understand to work with Doc-Ray correctly. Each page explains one concept, why it exists, how it relates to its neighbors, and where its limits are.

## The core nouns

- [Corpus and documents](./corpus-and-documents.md) — the Corpus you can see, the Document as an ingested file plus its derived material, and how document status differs from lifecycle state.
- [Document lifecycle](./document-lifecycle.md) — the Active, Archived, Deleted, and Purged states, what each does, and which are reversible.
- [Collections](./collections.md) — manual Collections you curate by hand and smart Collections defined by a rule.
- [Lenses](./lenses.md) — a Lens as a named scope and the Global lens, with the note that a Lens is metadata only today.
- [Entities and canonicalization](./entities-and-canonicalization.md) — Entity Mentions, Canonical Entities, the seeding workflow, entity profiles, and typeConsistency.
- [Entity Mentions](./entity-mentions.md) — observed named spans, their types, and how they differ from resolved identities.
- [Annotations](./annotations.md) — the linguistic layers over a Document: Annotation, Sentence, Span, and Lexeme.
- [Sentences](./sentences.md) — ordered, addressable text units used for reading, search, and citations.
- [Spans](./spans.md) — half-open character ranges that anchor annotations to extracted text.
- [Jobs](./jobs.md) — the background Job every API write enqueues, and the ingest pipeline chain.
- [Backups](./backups.md) — what a Backup captures, how retention works, and restore at a conceptual level.
- [Access and roles](./access-and-roles.md) — the Reader, Member, Curator, and Administrator tiers and the owner-plus-shared-plus-Administrator visibility rule.

## Where to go next

- To perform a task, see the [how-to guides](../how-to/).
- For the exact contract of a field, tool, command, or variable, see the [reference](../reference/).
