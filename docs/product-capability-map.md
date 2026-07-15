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
   └─ agents CURATE it (collections/catalog/titles/  ← mutations EXIST;
      descriptions/tags/lifecycle) ...............     gap = expose via MCP write scope
        ▲
        │ (optional) re-ingest — the ONE normal front door
        │
 Composer (separate) ── produces ──▶ Documents (standalone; never a lib row)
```

## The one-line thesis

**You built the hard ~75% everyone else fakes — a verifiable knowledge substrate
with enterprise governance — plus most of the *curation* write surface. Two things
remain: (a) let agents drive that curation (a small extension of today's read-only
MCP server), and (b) the Composer, which is greenfield but decoupled — it emits
standalone documents and only rejoins the lib via existing ingest.** [`CLAUDE.md`](../CLAUDE.md)'s
**PALS's Law** is the differentiator on both: the metadata **suggest → decide**
loop already is "agent proposes → verified → applied," and composition is grounded
against a canonical substrate. The moat is done; the frontier is agent-driven.

---

## Capability map — vision vs. as-built

| Pillar | Built today (evidence) | Maturity | Gap to the goal |
|---|---|---|---|
| **1. Living Library** | Multi-format ingest + OCR path; content-addressed originals (S3/MinIO); document lifecycle & history ([051](../db/migrations/051_document_lifecycle.sql), [045](../db/migrations/045_document_metadata_history.sql)); collections ([047](../db/migrations/047_document_collections.sql)); pages ([069](../db/migrations/069_document_pages.sql)); async jobs + progress ([020](../db/migrations/020_jobs.sql), [059](../db/migrations/059_job_progress.sql)); backups ([026](../db/migrations/026_backups.sql)); reannotate/reextract | **High** as a *library*; **partial** as *living* | It grows on ingest but does not *evolve*: no autonomous freshness, no cross-document relationship graph that updates, no "the library reacts to new material" |
| **2. Ultimate Knowledge Base** | 20-layer annotation graph (spans/tokens/morphology/syntax/reference/meaning-discourse, [007](../db/migrations/007_spans.sql)–[011](../db/migrations/011_meaning_discourse.sql)); lexemes ([005](../db/migrations/005_lexemes.sql)); embeddings ([012](../db/migrations/012_embeddings.sql)); **entity canonicalization to Wikidata** ([021](../db/migrations/021_canonical.sql)–[023](../db/migrations/023_resolution.sql)); semantic enrichment; evaluation/drift ([036](../db/migrations/036_evaluation_drift.sql)) | **High — this is the moat** | Mostly *depth* items: concept-level (not just entity-level) linking, ontology/relationships between hard concepts, cross-doc knowledge graph queries |
| **3. Document Composer** | **Nothing generative** — and correctly so, since compositions live *outside* the lib. GenAI reach today = entity resolution only (`resolver_{claude,ollama}.py`). Re-entry path for composed docs already exists: the standard ingest front door | **Greenfield (decoupled)** | A compose engine that emits **standalone documents** (never a corpus row): intent → retrieve grounding from the KB → GenAI draft → verify claims against the substrate → cited output. Re-ingestion is the *existing* path, unchanged |
| **(3b) Agent curation of the Library** | Collection write ops ([mutation_collections.py](../src/doc_ray/entrypoints/api/mutation_collections.py)); `update_document_metadata` (title/description/tags); **AI-suggest → human-decide metadata loop** (`suggest_document_metadata` / `decide_document_metadata_suggestions`, [055](../db/migrations/055_document_metadata_suggestions.sql)); lifecycle (archive/restore/bulk) | **Mostly built** | The mutations exist; the **only** gap is that the MCP server is read-only. Expose curation as MCP write tools behind a `curate`/`write` scope. This is the near-term, high-leverage move |
| **4. Inspiration from Prior Art** | Retrieval substrate exists: language-aware FTS ([016](../db/migrations/016_fts_language_aware.sql)), `searchSentences` / `fullTextSearch` / `entityFrequency` / `lexeme KWIC`; embeddings **computed & stored** ([012](../db/migrations/012_embeddings.sql), `semantic_enrich_document`) | **Substrate ~70%, product ~20%** | Vector-similarity *retrieval as a query surface*; "find prior art relevant to *this* intent"; concept-based (not keyword) discovery; synthesis of what's found |
| **5. Mix & Match (Hard × GenAI)** | Hard side ✓ (canonical entities, annotation graph, verifiable structure). GenAI plumbing ✓ (Anthropic/Ollama backends, **MCP server** for agent access). PALS's Law verification architecture ✓ (principle, in `CLAUDE.md`) | **Ingredients present, dish not cooked** | The fusion orchestration: hard-concept grounding → GenAI generation → verification against the substrate → cited, checkable output |

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

1. **Agent curation of the Library (3b) — nearest win, mostly built.** Expose the
   existing curation mutations (collections, title/description/tags, lifecycle) as
   MCP write tools behind a `curate` scope, reusing the **suggest → decide**
   verification loop so agents *propose* and a human or policy *approves*. This is an
   M-sized extension of the read-only MCP server shipped today — not greenfield. It is
   also what makes the library "living" in the sense described: agent-catalogued.
2. **Composer (Pillar 3) — greenfield, decoupled, highest ceiling.** A service that
   emits **standalone documents**: intent → retrieve grounding from the KB → GenAI
   draft → verify claims against the substrate (PALS's Law) → cited output. Output
   rejoins the lib only via existing ingest — **no new lib-write path**.
3. **Inspiration retrieval (Pillar 4) — half-built, fuels both.** Expose embeddings
   as a similarity-search surface + "prior art for intent X." Build alongside #1/#2.
4. **Living/evolving graph (Pillar 1) — depth, not launch-blocking.** A concept/
   relationship graph that updates as the library grows. Deferrable; #1 already
   delivers the "living" quality for a first release.

## Cross-cutting, before any of the three ships as a *product*

- **PURPOSE.md** — does not exist. Now that the goal is stated, it should be written
  and ratified (see below). It is the gate that unblocks the two dormant MANDATORY
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
- **Does not decide (operator's call):** the go-to-market shape, pricing, and the exact
  wording of the vision. The next artifact is a **draft `PURPOSE.md`** formalized from
  the stated goal above — for operator ratification, not agent fiat.
