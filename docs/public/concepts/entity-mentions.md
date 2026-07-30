---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Entity Mentions"
doc_type: "concept"
audience: ["end-user", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Entity Mentions

An **Entity Mention** is one observed span of text that a recognizer classified
as a person, place, organization, date, or another supported entity type. Two
mentions with the same surface text are still two observations. A mention is
not, by itself, a claim that both observations identify the same thing.

Each mention stays anchored to its Document by character offsets. This makes a
classification or boundary correction inspectable against the extracted text.
Corrections can reclassify a mention, reject a false positive, add a missed
mention, or adjust its span.

A **Canonical Entity** is different: it is a resolved identity that one or more
mentions may link to. Resolution can be global or Lens-scoped, and an unresolved
mention remains explicit rather than being forced into a guess.

## Why the distinction matters

- Mention counts describe observations in text, not unique real-world entities.
- Correcting a mention changes what the text is understood to contain; it does
  not silently rewrite the canonical registry.
- Canonicalization must preserve the source mention and its offsets so a
  reviewer can verify why a link was proposed or confirmed.

Related: [Entities and canonicalization](entities-and-canonicalization.md) ·
[Spans](spans.md) · [Correct an entity](../how-to/correct-an-entity.md)
