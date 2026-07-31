---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Entities and canonicalization"
doc_type: "concept"
audience: ["end-user", "evaluator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.2"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Entities and canonicalization

This page defines an Entity Mention and a Canonical Entity, explains the seeding workflow that populates the registry, and states that canonicalization links only to entities already seeded.

## Definition

An **Entity Mention** is a single occurrence, inside one Document, of a person, place, date, or organization. Each mention has a type and a Span marking where it sits in the text. The same real-world thing can be mentioned many times, across many Documents, under different spellings.

A **Canonical Entity** is one shared identity that many mentions point to. Canonicalization is the act of linking mentions to the Canonical Entity they refer to, so "R. Okonkwo", "Rita Okonkwo", and "Ms. Okonkwo" resolve to a single identity rather than three unrelated names.

## Why it exists and when it applies

Raw mention detection scatters one identity across spellings and types. Canonicalization consolidates them so that "who and what is this Corpus about" has a single, countable answer per identity. This concept applies whenever you ask about the people, places, dates, or organizations in your Corpus.

## The seeding workflow

Canonicalization **links mentions only to Canonical Entities that already exist in the registry.** It does not invent new identities on its own. The registry is populated first, through seeding:

1. **Suggest seeds** — Doc-Ray proposes candidate identities drawn from the mentions in your Corpus.
2. **Decide seeds** — review the candidates and approve, reject, or adjust them.
3. **Approve seeds** — the approved identities become Canonical Entities in the registry.

Only after identities are seeded does canonicalization have targets to link mentions to. Run seeding before expecting mentions to resolve.

## Entity profiles and typeConsistency

An **entity profile** merges one entity's mentions across the split NER types and spellings that raw detection produces, and reports a `typeConsistency` value. A low `typeConsistency` means the entity's type is **contested** — different mentions disagreed about whether it is, say, a person or an organization — not that the entity is unimportant. Prefer entity profiles over raw entity frequency when answering "who" or "what," because frequency counts each spelling and type separately while a profile reports the consolidated identity and the disagreement.

## Correcting mentions

When a mention is wrong, correct it directly. There are four corrections:

- **Reclassify** — the mention has the wrong type; supply the correct type.
- **Reject** — the marked text is not an entity.
- **Add** — an entity was missed; add it.
- **Adjust span** — the boundaries are wrong; fix the Span.

## How it relates to neighboring concepts

- **Annotations, Spans, and Sentences** — a mention is anchored to a Span within a Sentence; entity detection is one of the linguistic layers.
- **Lenses** — a Lens names a scope for future scoped resolution; today it does not change how mentions resolve.
- **Jobs** — canonicalization runs as background work; the full ingest pipeline chains annotation, semantic enrichment, and canonicalization automatically.

## Limitations

- Canonicalization links mentions only to already-seeded Canonical Entities. With an empty registry, mentions have nothing to resolve to. Seed first.
- A low `typeConsistency` marks a contested type, not an unimportant entity.
- Entity results carry a confidence; treat low-confidence rows as untrusted.
- A Lens does not currently scope resolution.

## Related how-tos and reference

- How-to: [Seed entities](../how-to/seed-canonical-entities.md)
- How-to: [Correct an entity](../how-to/correct-an-entity.md)
- Concept: [Annotations](./annotations.md)
- Concept: [Lenses](./lenses.md)
- Concept: [Jobs](./jobs.md)
- Reference: [GraphQL API](../reference/api.md)
- Reference: [MCP server](../reference/mcp.md)
