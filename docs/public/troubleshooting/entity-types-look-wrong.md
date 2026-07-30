---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Entity types look wrong"
doc_type: "troubleshooting"
audience: ["end-user", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Entity types look wrong

An entity is labelled with the wrong type — a person tagged as an organization, a
place tagged as a person — or its type seems to change from one mention to the next.

## Likely cause

An entity's mentions can be labelled with more than one type across a document.
Doc-Ray merges those mentions into a single entity profile and reports how much the
types disagree as `typeConsistency`. A low `typeConsistency` means the type is
contested across mentions, not that the entity is unimportant or that the profile
is broken. The shown type is the reconciled result, and a specific mention can
still be labelled incorrectly.

## Resolution

1. Read the entity profile rather than raw mention counts. The profile merges the
   split types and reports `typeConsistency` so a contested type is visible.
2. Locate the specific mention that is labelled wrong.
3. Correct that mention. Choose the correction that matches the problem:
   - **Reclassify** when the mention is a real entity of a different type; give the
     correct type.
   - **Reject** when the mention is not an entity at all.
   - **Add** when a real entity was missed.
   - **Adjust span** when the entity is real and correctly typed but its boundaries
     are wrong.
   See [How-To: Correct an entity](../how-to/correct-an-entity.md).
4. Re-read the profile. Corrections shift the reconciled type and raise
   `typeConsistency` as the contested mentions are resolved.

## Confirm it is resolved

The corrected mention carries the type you set, and the entity profile's
`typeConsistency` reflects the reduced disagreement.

## Escalation boundary

Correcting a mention is a supported Curator action. A low `typeConsistency` on an
entity whose type is genuinely ambiguous in the source text is expected and is not
an error. If a correction does not persist for a document you can curate, that is a
support matter.

---

Related: [Concept: Canonical entities](../concepts/entities-and-canonicalization.md) ·
[Concept: Entity mentions](../concepts/entities-and-canonicalization.md) ·
[How-To: Correct an entity](../how-to/correct-an-entity.md) ·
[Troubleshooting](index.md)
