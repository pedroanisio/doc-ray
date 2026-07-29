---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-29"
title: "Spans"
doc_type: "concept"
audience: ["end-user", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.54.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Spans

A **Span** is a half-open character range, `[charStart, charEnd)`, in a
Document's extracted full text. The start character is included and the end
character is excluded. That convention makes adjacent ranges unambiguous and
lets a client recover the exact surface with ordinary string slicing.

Sentences, tokens, chunks, Entity Mentions, and other annotations refer to the
shared Span registry instead of copying their own offsets. This keeps derived
layers aligned to one text coordinate system and makes every surface claim
traceable back to source text.

Offsets are stable for a given extracted-text version, not across arbitrary
re-extraction. When extraction changes, dependent annotations are regenerated
against the new text. Evidence intended to survive that transition should keep
the Document id and quoted surface as well as the numeric range.

Related: [Sentences](sentences.md) · [Entity Mentions](entity-mentions.md) ·
[Annotations](annotations.md)
