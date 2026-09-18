---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-09-18"
doc_type: design
status: current
applies_to: ">=1.48.0"
---

# The Entity Fabric — Substrate · Lens · Projection

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

This is the design record for how doc-ray resolves entities across a mixed
corpus. Where it names a table or a migration, that is the shipped
implementation; the section numbers are stable because migrations and source
modules cite them (`115_ner_corrections.sql`, `121_lens_registry.sql`,
`127_document_classification.sql`, `129_entity_projection.sql`,
`133_lens_rollups.sql`, `domain/lenses.py`, `domain/projection.py`).

## 1. The requirement

[PURPOSE.md](../../PURPOSE.md) is unambiguous about the point of doc-ray:
**entities are the connective tissue.** The same person, place, date, or
organization, resolved to one shared identity, threads scattered mentions into
"a single connected fabric of concepts," and *"connecting a large body of
concepts through the entities themselves is a first-class goal, not a side
effect."* On that fabric, prior work inspires new work and generation stays
accountable to its sources.

The corpus this fabric must carry is neither small nor uniform: **thousands of
documents, fiction and non-fiction side by side.** That scale and that mixture
turn four qualities into architectural requirements:

- **Completeness** — every mention, every source, every uncertainty preserved; nothing flattened into a lossy merge.
- **Hyper-context** — dense connection: an idea reachable from its neighbours, its entities, its allusions, and the corpus-wide sense of each entity.
- **Reuse** — one lexicon, one identity registry, one embedding space; related work finds related work.
- **Global and local** — real-world identities shared across the whole corpus, *and* fictional identities that stay inside their world.

The mixture is where naïve designs fail. "London" in *1984* is a dystopian
fiction; "London" in a war history is the real city. "Napoleon" in *War and
Peace* is a character; in a biography he is the man. A single global registry
that merges these produces **false reuse** — the precise inverse of
completeness — and quietly corrupts the fabric the whole product rests on.
Resolving a large corpus's entities is not a search feature bolted on; it is
the load-bearing wall.

## 2. Invariants

These come from PURPOSE.md and from the verification requirement that governs
every model-produced value in doc-ray. They constrain every decision below.

1. **Source before synthesis.** Every derived thing traces to the spans,
   mentions, and decisions that support it. Provenance is not metadata; it is
   the substrate.
2. **AI proposes, verification decides.** NER and canonicalization are
   unverified model output by default. Low confidence *abstains and routes to
   review*; it never asserts a guess. Verification is a design layer.
3. **Human direction over autonomous drift.** Durable truth — shared
   identities, deletion, publication, catalog structure — passes through
   explicit human or policy review.
4. **Curate the library; compose outside it.** The architecture shapes what the
   library holds. It never writes generated content back into the library.
5. **Personal flow and organizational control on one corpus.** The same store
   serves a solo reader and a tenant with access/audit/approval needs — so
   isolation (RLS, tenancy) is not optional and not a later bolt-on.

Any change that improves connection at the cost of provenance, verifiability,
or isolation is disqualified, regardless of how much "smarter" it looks.

## 3. The model — Substrate · Lens · Projection (SLP)

The central move is to **separate the immutable ground truth from its
contextual interpretation**, and to make interpretation a *derived, named,
re-computable view* rather than a fact baked into the data.

```
        ┌──────────────────────────── PROJECTIONS (derived, materialized) ─────────────────────────────┐
        │  global lens         │  "Tolkien Legendarium" lens   │  "WWII Histories" lens   │   …          │
        │  Napoleon→Q517       │  Gandalf→wizard#1             │  Napoleon→Q517           │              │
        │  London →Q84         │  London →dystopian-london#1   │  London →Q84             │              │
        └───────────▲──────────────────────▲──────────────────────────▲──────────────────────────────────┘
                    │  project(mention, lens)  — re-derivable, reversible, refreshed like a rollup
        ┌───────────┴────────────────────── LENSES (named scopes; who/what interprets) ──────────────────┐
        │  a designated collection == a lens; `global` is the default lens; lenses may overlap freely     │
        └───────────▲──────────────────────────────────────────────────────────────────────────────────┘
                    │  membership + genre classification select which lenses see a document
        ┌───────────┴────────────────────── SUBSTRATE (immutable ground truth, global, complete) ────────┐
        │  spans · sentences · lexemes (one identity per form) · raw entity_mentions · embeddings ·        │
        │  human corrections · provenance — never merged away, always the source of every projection      │
        └──────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Substrate — the immutable ground truth

Everything factual and complete lives here and is **never mutated by
interpretation**: `spans`, `sentences`, the `lexemes` registry (a global
identity — `UNIQUE (lang, pos, lemma)`, migration 005), raw `entity_mention`
rows (the NER output — surface + type + span, migration 010), embeddings, and
**human corrections**. The substrate is append-mostly: a mention is a *fact that
the model produced this span with this type*, not a claim about the world.

Two consequences:

- **Completeness is structural, not a policy.** A mention is never deleted to
  deduplicate; two "Londons" are never merged into one row. The substrate keeps
  them distinct and lets projections decide.
- **Corrections attach here, not to the interpretation.** When a reviewer fixes
  a mis-typed entity, the correction is a substrate fact keyed by a *stable span
  identity* (document + char range), because `entity_mention` rows are
  ephemeral — they are `ON DELETE CASCADE` from `annotations` (migration 010)
  and are wiped on every re-annotate. A fix written to the substrate re-applies
  through *every* projection, forever (`entity_mention_correction`, migration
  115).

### 3.2 Lens — the named scope that interprets

A **lens** is a named context under which the substrate is resolved. Two
tiers, and the distinction is the whole game:

- **`global`** — the default lens. Real-world identities (Wikidata-linkable;
  `canonical.wikidata_id`, migration 021). Non-fiction resolves here and reuses
  maximally.
- **Scope lenses** — a *designated* `document_collections` row
  (`is_scope`, migration 121). "Tolkien Legendarium," "the MCU," a single
  standalone novel. A lens groups the documents whose fictional identities
  canonicalize *together*.

**Lenses may overlap.** A document can be seen through several lenses because a
projection is a *view*, not an owner. A historical novel is projected through
`global` (its real entities) *and* its work lens (its invented ones) at once. A
document in the ordinary organizational collections "Fantasy" and "To read"
gains no lens from them; only *designated* scope collections project. Lenses
may nest (`parent_lens_id`); resolution runs inner → outer → global. A document
names one default lens for display (`documents.default_lens_id`).

Ordinary collections are unchanged: overlapping, organizational,
identity-neutral. Only the scope flag makes a collection a lens.

### 3.3 Projection — the derived, verifiable interpretation

The entity registry is not stored on the substrate; it is
`entity = project(mention, lens)` — a **materialized, re-computable view**
(`entity_projection`, `lens_local_entity`, `entity_projection_review`,
migration 129), refreshed exactly like the D-024 read-model rollups
(`lexeme_stats`, `document_stats`). The same substrate mention projects to Q517
under `global` and to a fictional identity under a work lens.

Because projections are derived:

- They are **reversible** — a bad projection is dropped and re-derived; the
  substrate is untouched. This is what makes "AI proposes, verification decides"
  safe at scale: the guess is never load-bearing.
- They are **composable** — the "hyper-context" answer to an entity is *its
  projection under a lens, plus its allusion edges to other lenses*. Query
  "Gandalf through the Tolkien lens ∪ real-world allusions."
- They **default sensibly** — a document declares one default lens for display;
  everything else is opt-in depth.

An **allusion** edge (`entity_allusion`, migration 131) is how fiction reaches
the real world without merging: *the character Napoleon in this novel alludes
to Q517* — an edge, never an identity equation.

Genre classification (`document_classification` + `domain.genre_policy`,
migration 127) decides, per entity type, whether a document's mentions project
locally or globally: fictional types go to the work lens, real-world types to
`global`, and an uncertain genre abstains to review rather than guessing.

## 4. How SLP delivers the four requirements

| Requirement | Delivered by |
| --- | --- |
| **Completeness** | Substrate is immutable and lossless; mentions/corrections/provenance never merged away; uncertainty preserved as *abstention*, not a forced guess. |
| **Hyper-context** | Projections are composable lenses; allusion edges cross scopes; a per-lens rollup (`lens_entity_rollup`, `lens_entity_relation` — co-occurrence NPMI and bounded N-hop traversal, migration 133) gives the corpus-wide sense of an entity alongside its local neighbourhood. |
| **Reuse** | One substrate — one `lexemes` registry, one mention set, one embedding space — shared by every lens; `global` maximizes real-world reuse. |
| **Global & local** | `global` lens vs. scope lenses; resolution is local-first, global-fallback; enforced with the same RLS discipline that isolates tenants. |
| **Fiction vs. non-fiction** | Genre classification routes a document's fictional types to its work lens and real-world types to `global`; the same document participates in both — no lossy either/or. |

## 5. Prior art the design borrows from

This design is deliberately unoriginal at the level of *ideas*; it assembles
patterns each proven at scale:

- **Datomic / Rich Hickey — "immutable facts + as-of views."** A database as an
  accumulating set of immutable facts, with queries that project a value of the
  DB "as of" a point or filter. *Borrowed:* substrate = immutable facts;
  projection = a derived value of the fabric under a lens.
- **Event Sourcing / CQRS (Fowler, Young).** Append-only log of what happened;
  read models are derived and rebuildable. *Borrowed:* projections are read
  models refreshed from the substrate; a wrong one is rebuilt, never patched in
  place.
- **RDF 1.1 named graphs / SPARQL (W3C).** The graph/context is the fourth
  element that scopes a statement. *Borrowed:* the lens as the context
  dimension — "same statement, different world."
- **Wikidata & VIAF authority control.** Global canonical identifiers for
  real-world entities — and fictional characters modelled as a distinct entity
  type, not conflated with real people. *Borrowed:* `global` as the real-world
  authority; fiction as its own kind, resolved under its own lens.
- **IFLA FRBR / LRM.** Work → Expression → Manifestation → Item separates the
  work from its editions. *Borrowed:* a scope lens for a series behaves like a
  Work/aggregate over its expressions.
- **WordNet & word-sense disambiguation (Miller, Fellbaum).** A lexeme is
  invariant; its sense is contextual. *Borrowed:* `project(lexeme/mention,
  lens) → sense/entity`; the entity layer is the lexeme layer's sense
  projection.
- **Microsoft GraphRAG (2024).** An entity graph plus hierarchical community
  summaries yields corpus-wide sensemaking *and* local retrieval. *Borrowed:* a
  per-lens rollup so an agent gets both "what this passage says" and "what the
  library knows about this entity."
- **Neo4j GDS graph projections.** Project a named subgraph from the global
  graph, run algorithms on it, refresh, discard. *Borrowed:* the operational
  model of a lens as a projected, materialized subgraph.
- **W3C PROV-O.** Corrections and projections record their agent and source,
  making the fabric auditable — the backbone of "source before synthesis."
- **Vertica projections.** Derived storage over one logical base, materialized
  for read performance and refreshed, never the source of truth.

The through-line: **keep an immutable, complete base; make interpretation a
named, derived, rebuildable view.**

## 6. Human review as a substrate operation

Entity correction is how "AI proposes, verification decides" becomes real:

- A correction (reclassify / reject / add / adjust-span) is written to the
  **substrate**, keyed by a stable `(document_id, char_start, char_end)` span
  identity — durable across the ephemeral, re-annotated `entity_mention` rows.
- It records agent, timestamp, original model value, and rationale, and lands
  an `audit_events` row.
- Every lens **re-projects** from the corrected substrate — one fix, corrected
  everywhere, with no lens fighting the model.

## 7. Schema — additive over the substrate

Each of these is additive over the cited table; none rewrites the substrate.

1. **`document_collections`** — `is_scope` and `parent_lens_id` mark a
   collection as a lens; `documents.default_lens_id` names a document's display
   lens (migration 121). Ordinary collections are unchanged.
2. **Projection dimension** — `entity_projection` carries `lens_id`; resolution
   runs *per lens* and is materialized. There is no one-scope-per-document
   partition; lenses overlap (migration 129).
3. **`entity_allusion`** — a typed cross-lens link `(from entity, to canonical,
   lens_from, lens_to, actor)`; never an identity equation (migration 131).
4. **`entity_mention_correction`** — `(document_id, char_start, char_end,
   action, corrected_type, corrected_span, original_type, reviewer, rationale,
   created_at)` with RLS mirroring document scope; re-applied by the annotate
   pipeline (migration 115).
5. **Per-lens rollups** — `lens_entity_rollup` and `lens_entity_relation`,
   materialized like `document_stats` / `lexeme_stats` and refreshed by a job
   (migration 133).
6. **Genre axis on `document_classification`** — fiction/non-fiction (and
   finer) feeding the per-entity-type resolution policy (migration 127).

## 8. What SLP does not change

- The substrate's existing identities: `lexemes` stays the global vocabulary
  registry; raw `entity_mention` stays the NER record.
- Tenancy and RLS: lenses sit *inside* a tenant; a lens never crosses tenant
  isolation.
- "Compose outside the library": projections and rollups are curation of what
  the library holds; they never write generated documents back in.
- Human authority over durable truth: promoting a collection to a lens, drawing
  an allusion, and confirming a projection are review actions, not autonomous
  writes.

---

**One-line summary.** Keep an immutable, complete substrate of facts; make every
entity identity a named, derived, rebuildable projection through a lens; let
collections name the lenses and humans confirm the projections.

[← root README](../../README.md) · [PURPOSE.md](../../PURPOSE.md) · [Architecture](../architecture.md)
