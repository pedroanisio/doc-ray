---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Canonicalization links nothing"
doc_type: "troubleshooting"
audience: ["end-user", "curator", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.55.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Canonicalization links nothing

Running canonicalization over a document resolves almost none of its mentions to
canonical entities, and the entity registry stays nearly empty.

## Likely cause

Canonicalization links entity mentions only to entities that already exist in the
registry. When the registry is empty or holds none of the identities in your
document, there is nothing for the mentions to link to, so almost nothing resolves.
This is expected on a fresh corpus: the registry must be populated first.

## Resolution

1. Seed the registry before expecting links. Populate it with the identities you
   care about using the seeding workflow: review the suggested seeds, decide which
   to accept, and approve them.
   See [How-To: Seed and curate entities](../how-to/seed-canonical-entities.md).
2. Run canonicalization again after the registry holds those identities. Mentions
   that match a registered entity now link to it.
3. If some mentions still do not link, seed the additional identities they refer to
   and re-run. Canonicalization never invents an entity that was not seeded.

## Confirm it is resolved

After seeding and re-running, mentions in the document link to canonical entities,
and those entities appear in the registry with their linked mentions.

## Escalation boundary

Seeding the registry is a supported Curator action and is the intended first step;
an empty result before seeding is not a defect. Deciding which identities belong in
the registry is a curation judgment, not a mechanical one. If seeded, approved
entities still fail to link mentions that clearly match them, that is a support
matter.

---

Related: [Concept: Canonical entities](../concepts/entities-and-canonicalization.md) ·
[How-To: Seed and curate entities](../how-to/seed-canonical-entities.md) ·
[FAQ](../faq/index.md#why-does-canonicalization-link-almost-nothing) ·
[Troubleshooting](index.md)
