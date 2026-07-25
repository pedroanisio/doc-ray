---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-15"
---

# doc-ray — Product Capability Map

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

**Purpose of this doc:** map the operator-stated product goal against what the
codebase *actually* implements (with evidence), so the launch scope can be
decided from fact rather than intuition. It is a capability map, **not** a
`PURPOSE.md` — the vision below is quoted from the operator, not inferred.

---

## The stated goal (operator's words)

> **Living Library + Ultimate Knowledge Base + Document Composer + Inspiration
> from Prior Art + Mix and Match of Hard Concepts and GenAI creativity.**

Read as five pillars:

1. **Living Library** — a corpus that grows, versions, and stays current.
2. **Ultimate Knowledge Base** — a rigorous, canonical, verifiable substrate of
   *hard concepts* (entities, structure, meaning).
3. **Document Composer** — *generate new documents*, not just store them.
4. **Inspiration from Prior Art** — surface relevant existing knowledge to seed
   new work.
5. **Mix & Match: Hard Concepts × GenAI creativity** — fuse the verifiable
   substrate with generative creativity, using the hard side to keep GenAI honest.

## Two write surfaces — kept strictly separate (operator clarification)

A correction that shapes everything below: **composition never writes into the
Library.** There are two distinct write surfaces, and only one touches the lib:

1. **Curation of the Library** — agents *reorganize* what already exists:
   collections, cataloging, titles, descriptions, tags, lifecycle. This is the
   near-term want, and it is **mostly built** (mutations already exist; see below).
2. **Composition of Documents** — produces **standalone documents**, *outside*
   the lib. A composition is never a corpus row. It can *later* re-enter the
   Library only through the **normal ingest front door** — same path as any other
   file. One write path in; no special-casing.

```
 Library (ingested substrate)
   ├─ agents READ it .............................. shipped (MCP read tools)
   └─ agents CURATE it (collections/Lenses/metadata/
      entities/visual decisions) ................. shipped (opt-in MCP write gate)
        ▲
        │ (optional) re-ingest — the ONE normal front door
        │
 Composer (separate) ── produces ──▶ Documents (standalone; never a lib row)
```

## The one-line thesis

**You built the hard ~75% everyone else fakes — a verifiable knowledge substrate
with enterprise governance — and aligned human/API/agent curation. A deterministic
cited briefing now grounds composition without pretending to be the Composer.
What remains is the generative, claim-verifying export stage; it stays decoupled,
emits standalone documents, and only rejoins the library through ingest.** [`CLAUDE.md`](../CLAUDE.md)'s
**PALS's Law** is the differentiator on both: the metadata **suggest → decide**
loop already is "agent proposes → verified → applied," and composition is grounded
against a canonical substrate. The moat is done; the frontier is agent-driven.

---

## Capability map — vision vs. as-built

| Pillar | Built today (evidence) | Maturity | Gap to the goal |
|---|---|---|---|
| **1. Living Library** | Multi-format ingest + OCR path; content-addressed originals (S3/MinIO); document lifecycle & history ([051](../db/migrations/051_document_lifecycle.sql), [045](../db/migrations/045_document_metadata_history.sql)); collections ([047](../db/migrations/047_document_collections.sql)); pages ([069](../db/migrations/069_document_pages.sql)); async jobs + progress ([020](../db/migrations/020_jobs.sql), [059](../db/migrations/059_job_progress.sql)); backups ([026](../db/migrations/026_backups.sql)); reannotate/reextract | **High** as a *library*; **partial** as *living* | It grows on ingest but does not *evolve*: no autonomous freshness, no cross-document relationship graph that updates, no "the library reacts to new material" |
| **2. Ultimate Knowledge Base** | 20-layer annotation graph (spans/tokens/morphology/syntax/reference/meaning-discourse, [007](../db/migrations/007_spans.sql)–[011](../db/migrations/011_meaning_discourse.sql)); lexemes ([005](../db/migrations/005_lexemes.sql)); embeddings ([012](../db/migrations/012_embeddings.sql)); **entity canonicalization to Wikidata** ([021](../db/migrations/021_canonical.sql)–[023](../db/migrations/023_resolution.sql)); semantic enrichment; evaluation/drift ([036](../db/migrations/036_evaluation_drift.sql)) | **High — this is the moat** | Mostly *depth* items: concept-level (not just entity-level) linking, ontology/relationships between hard concepts, cross-doc knowledge graph queries |
| **3. Document Composer** | The shared `briefing`/`composition` service now emits a deterministic **cited evidence pack** through GraphQL `briefingPack`, MCP `briefing_pack`, and `/briefing`; it deliberately does not generate prose or write derived claims. Re-entry for any externally composed document remains the standard ingest front door | **Grounding foundation built; generation remains decoupled** | A compose engine that turns the pack into a standalone document, verifies every claim against its citations, and exports without silently becoming corpus fact |
| **(3b) Agent curation of the Library** | Collection/Lens writes, metadata suggest→decide, durable Entity corrections, seed decisions, and visual-caption approve/reject are exposed through guarded GraphQL and opt-in MCP write tools; `/review` gives people the same decision lanes | **High — write paths aligned and human-gated** | Deeper workflow ergonomics and policy-specific approval routing, not another parallel write implementation |
| **4. Inspiration from Prior Art** | Language-aware FTS plus learned `semanticSearchSentences`, MCP semantic retrieval, web Meaning search, Prism image galleries, and `crossModalImageSearch` / web Images mode; sentence and image hits retain Document/ordinal/page provenance | **High retrieval substrate; synthesis remains bounded** | Semantic retrieval inside briefing composition, concept-graph discovery beyond embeddings, and evaluated recommendation quality |
| **5. Mix & Match (Hard × GenAI)** | Hard substrate ✓; agent access ✓; cited evidence assembly ✓; human review for metadata, Entity, and Tier-3 visual proposals ✓. `gaps` and citations make incomplete evidence visible before synthesis | **Verification-ready foundation** | The final generation/verification loop: compose prose, resolve every citation, reject unsupported claims, and export a standalone artifact |

### The governance layer (not a pillar, but why "not solo anymore" is true)

Multi-tenant foundation ([061](../db/migrations/061_tenant_foundation.sql)), SAML SSO
([067](../db/migrations/067_saml_identity_providers.sql)), SCIM provisioning
([065](../db/migrations/065_scim_provisioning.sql)), ABAC sensitive-doc policy
([064](../db/migrations/064_abac_sensitive_document_policy.sql)), audit events
([062](../db/migrations/062_security_audit_events.sql)), RBAC + per-principal RLS
([018](../db/migrations/018_rbac.sql)), local auth ([074](../db/migrations/074_local_auth.sql)).
This is enterprise-buyer plumbing — it means composed output can be **tenant-scoped
and access-governed by default**, which most GenAI authoring tools cannot claim.

---

## Where the launch work actually is

Ranked by leverage, corrected for the two-surface model:

1. **Composer (Pillar 3) — decoupled, highest ceiling.** A service that
   emits **standalone documents**: intent → retrieve grounding from the KB → GenAI
   draft → verify claims against the substrate (PALS's Law) → cited output. Output
   rejoins the lib only via existing ingest — **no new lib-write path**.
2. **Semantic briefing retrieval — bounded follow-up.** Meaning search is shipped,
   but briefing selection is intentionally lexical in 1.43.0. Lift it only with
   measured retrieval quality and the same visible gap contract.
3. **Approval-policy depth.** The Review inbox and MCP write gate are built; add
   organization-specific routing or multi-party approval only when policy demands it.
4. **Living/evolving graph (Pillar 1) — depth, not launch-blocking.** A concept/
   relationship graph that updates as the library grows. Deferrable; shipped
   curation already delivers the "living" quality for a first release.

## Cross-cutting, before any of the three ships as a *product*

- **PURPOSE.md** — exists and states the product boundary. Keep it ratified as
  composition expands; it is the gate that keeps the two MANDATORY
  rules in `CLAUDE.md`.
- **Positioning of the offering** — hosted vs. self-host, who onboards, pricing —
  still an operator decision; the tenancy/governance to support either is already built.
- **Composer trust surface** — because the whole pitch is "grounded, non-hallucinating,"
  the verification/citation UX *is* the product, not a nicety.

---

## What this map does and does not decide

- **Decides (from evidence):** the moat is the KB substrate + governance; the frontier
  is generative (Composer + Inspiration + Mix/Match); the hard foundation makes
  *grounded* composition uniquely feasible here.
- **Does not decide (operator's call):** the go-to-market shape, pricing, or any
  future change to the ratified `PURPOSE.md`.
