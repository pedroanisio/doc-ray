---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-24"
status: draft
---

# Architecture Proposal — The Entity Fabric at Scale: Substrate · Lens · Projection

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

> **Status: draft for review.** This proposes a target architecture and a
> delivery path. It does not change code. Every claim about the current schema
> is cited to a migration; every borrowed pattern is named to a real system.
> Where I give engineering judgment rather than established fact, I say so.

---

## 1. The mandate — what we must deliver

[PURPOSE.md](../../PURPOSE.md) is unambiguous about the point of doc-ray:
**entities are the connective tissue.** The same person, place, date, or
organization, resolved to one shared identity, threads scattered mentions into
"a single connected fabric of concepts," and *"connecting a large body of
concepts through the entities themselves is a first-class goal, not a side
effect."* On that fabric, prior work inspires new work and generation stays
accountable to its sources.

The corpus this fabric must carry is not small or uniform: **10,000+ documents,
fiction and non-fiction side by side.** That scale and that mixture turn four
qualities from nice-to-haves into architectural requirements:

- **Completeness** — every mention, every source, every uncertainty preserved; nothing flattened into a lossy merge.
- **Hyper-context** — dense connection: an idea reachable from its neighbours, its entities, its allusions, and the corpus-wide sense of each entity.
- **Reuse** — one lexicon, one identity registry, one embedding space; related work finds related work.
- **Global and local** — real-world identities shared across the whole corpus, *and* fictional identities that stay inside their world.

The mixture is where naïve designs fail. "London" in *1984* is a dystopian
fiction; "London" in a war history is the real city. "Napoleon" in *War and
Peace* is a character; in a biography he is the man. A single global registry
that merges these produces **false reuse** — the precise inverse of
completeness — and quietly corrupts the fabric the whole product rests on.
Resolving 10k documents' entities is not a search feature bolted on; it is the
load-bearing wall.

## 2. Invariants — what the architecture may not violate

These come from PURPOSE.md and CLAUDE.md and constrain every decision below.

1. **Source before synthesis.** Every derived thing traces to the spans,
   mentions, and decisions that support it. Provenance is not metadata; it is
   the substrate.
2. **AI proposes, verification decides (PALS's Law).** NER and canonicalization
   are unverified ML output by default. Low confidence *abstains and routes to
   review*; it never asserts a guess. Verification is a design layer.
3. **Human direction over autonomous drift.** Durable truth — shared
   identities, deletion, publication, catalog structure — passes through
   explicit human or policy review.
4. **Curate the library; compose outside it.** The architecture shapes what the
   library holds. It never writes generated content back into the library.
5. **Personal flow and organizational control on one corpus.** The same store
   serves a solo reader and a tenant with access/audit/approval needs — so
   isolation (RLS, tenancy) is not optional and not a later bolt-on.

Any proposal that improves connection at the cost of provenance, verifiability,
or isolation is disqualified — regardless of how much "smarter" it looks.

## 3. The proposal — Substrate · Lens · Projection (SLP)

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
interpretation**: `spans`, `sentences`, the `lexemes` registry (already a
global identity — `UNIQUE (lang, pos, lemma)`, migration 005), raw
`entity_mention` rows (the NER output — surface + type + span, migration 010),
embeddings, and **human corrections**. The substrate is append-mostly and
content-addressed in spirit: a mention is a *fact that the model produced this
span with this type*, not a claim about the world.

Two consequences:

- **Completeness is structural, not a policy.** You never delete a mention to
  deduplicate; you never merge two "Londons" into one row. The substrate keeps
  them distinct and lets projections decide.
- **Corrections attach here, not to the interpretation.** When a reviewer fixes
  a mis-typed entity (the "NER polishing" capability), the correction is a
  substrate fact keyed by a *stable span identity* (document + char range),
  because `entity_mention` rows are ephemeral — they are `ON DELETE CASCADE`
  from `annotations` (migration 010) and are wiped on every re-annotate. A fix
  written to the substrate re-applies through *every* projection, forever.

### 3.2 Lens — the named scope that interprets

A **lens** is a named context under which the substrate is resolved. Two
tiers, and the distinction is the whole game:

- **`global`** — the default lens. Real-world identities (Wikidata-linkable;
  `canonical.wikidata_id`, migration 021). Non-fiction resolves here and reuses
  maximally.
- **Scope lenses** — a *designated* `document_collections` row (migration 047
  already carries a `kind`; add a scope kind or `is_scope` flag). "Tolkien
  Legendarium," "the MCU," a single standalone novel. A lens groups the
  documents whose fictional identities canonicalize *together*.

Crucially — and this is the correction to an earlier partition-based sketch —
**lenses may overlap.** A document can be seen through several lenses because a
projection is a *view*, not an owner. A historical novel is projected through
`global` (its real entities) *and* its work lens (its invented ones) at once. A
document in the ordinary organizational collections "Fantasy" and "To read"
gains no lens from them; only *designated* scope collections project.

Ordinary collections stay exactly as they are today: overlapping, organizational,
identity-neutral. Only the scope flag makes a collection a lens.

### 3.3 Projection — the derived, verifiable interpretation

The entity registry is not stored on the substrate; it is
`entity = project(mention, lens)` — a **materialized, re-computable view**,
refreshed exactly like the D-024 read-model rollups (`lexeme_stats`,
`document_stats`) already are. `canonical` and `entity_link` become
lens-parameterized: the same substrate mention projects to Q517 under `global`
and to a fictional identity under a work lens.

Because projections are derived:

- They are **reversible** — a bad projection is dropped and re-derived; the
  substrate is untouched. This is what makes "AI proposes, verification decides"
  safe at 10k scale: the guess is never load-bearing.
- They are **composable** — the "hyper-context" answer to an entity is *its
  projection under a lens, plus its allusion edges to other lenses*. Query
  "Gandalf through the Tolkien lens ∪ real-world allusions."
- They **default sensibly** — a document declares one default lens for display;
  everything else is opt-in depth.

An **allusion** edge (a first-class cross-lens link) is how fiction reaches the
real world without merging: *the character Napoleon in this novel alludes to
Q517* — an edge, never an identity equation.

## 4. How SLP delivers the four requirements

| Requirement | Delivered by |
| --- | --- |
| **Completeness** | Substrate is immutable and lossless; mentions/corrections/provenance never merged away; uncertainty preserved as *abstention*, not a forced guess. |
| **Hyper-context** | Projections are composable lenses; allusion edges cross scopes; a per-lens community-summary rollup (GraphRAG-style, §5) gives the corpus-wide sense of an entity alongside its local neighbourhood. |
| **Reuse** | One substrate — one `lexemes` registry, one mention set, one embedding space — shared by every lens; `global` maximizes real-world reuse. |
| **Global & local** | `global` lens vs. scope lenses; resolution is local-first, global-fallback; enforced with the same RLS discipline that already isolates tenants. |
| **Fiction vs. non-fiction** | Genre classification (extend `document_classification`) routes a document's fictional types to its work lens and real-world types to `global`; the same document participates in both — no lossy either/or. |

## 5. What the greatest architects already built — and what we borrow

This design is deliberately unoriginal at the level of *ideas*; it assembles
patterns each proven at scale. Named honestly, and mapped to what we take:

- **Datomic / Rich Hickey — "immutable facts + as-of views."** A database as an
  accumulating set of immutable facts, with queries that project a value of the
  DB "as of" a point or filter. *We borrow the spine:* substrate = immutable
  facts; projection = a derived value of the fabric under a lens. "Value vs.
  place" is exactly why interpretation must not overwrite ground truth.
- **Event Sourcing / CQRS (Fowler, Young).** Append-only log of what happened;
  read models are derived and rebuildable. *We borrow:* projections are read
  models refreshed from the substrate; a wrong one is rebuilt, never patched in
  place.
- **RDF 1.1 named graphs / SPARQL (W3C, Berners-Lee lineage).** The *graph/
  context* is the fourth element that scopes a statement; entailment regimes are
  projections of one store. *We borrow:* the lens as the context dimension —
  "same statement, different world."
- **Wikidata & VIAF authority control.** Global canonical identifiers (Q-ids,
  VIAF clusters) for real-world entities — and, tellingly, **fictional
  characters modelled as a distinct entity type**, not conflated with real
  people. *We borrow:* `global` as the real-world authority; fiction as its own
  kind, resolved under its own lens.
- **IFLA FRBR / LRM (library science).** Work → Expression → Manifestation →
  Item separates *the work* from *its editions*. *We borrow:* a scope lens for a
  series behaves like a Work/aggregate over its expressions — essential once you
  hold many editions/translations of one book.
- **WordNet & Word-Sense Disambiguation (Miller, Fellbaum).** A *lexeme* is
  invariant; its *sense* is contextual. *We borrow the theory of the whole
  model:* `project(lexeme/mention, lens) → sense/entity`. doc-ray already has
  the lexeme layer; the entity layer is its sense projection.
- **Microsoft GraphRAG (2024).** An entity graph plus **hierarchical community
  summaries** yields corpus-wide "sensemaking" *and* local retrieval. *We
  borrow:* a per-lens summary rollup so an agent gets both "what this passage
  says" and "what the library knows about this entity."
- **Neo4j GDS graph projections.** Project a named subgraph from the global
  graph, run algorithms on the lens, refresh, discard. *We borrow:* the literal
  operational model of a lens as a projected, materialized subgraph.
- **Palantir Foundry ontology.** One governed object/relationship model over
  heterogeneous data, with lineage and access control as first-class. *We
  borrow the posture:* the fabric is a governed ontology, not a pile of
  extractions — provenance and RLS are part of the model.
- **W3C PROV-O.** A standard vocabulary for "entity/activity/agent derived
  from." *We borrow:* corrections and projections record their agent and source,
  making the fabric auditable — the backbone of "source before synthesis."
- **Vertica projections (Stonebraker lineage).** Physical, query-optimized
  derived storage over one logical base. *We borrow the word and the discipline:*
  projections are materialized for read performance and refreshed, never the
  source of truth.

The through-line every one of these shares: **keep an immutable, complete base;
make interpretation a named, derived, rebuildable view.** SLP is that lesson
applied to an entity fabric.

## 6. Human-in-the-loop as a substrate operation

The NER-correction capability discussed separately is not a side feature; it is
how PURPOSE.md's "AI proposes, verification decides" becomes real, and SLP makes
it clean:

- A correction (reclassify / reject / add / adjust-span) is written to the
  **substrate**, keyed by a stable `(document_id, char_start, char_end)` span
  identity — durable across the ephemeral, re-annotated `entity_mention` rows.
- It records agent, timestamp, original ML value, and rationale (PROV-style),
  and lands an `audit_events` row.
- Every lens **re-projects** from the corrected substrate — one fix, corrected
  everywhere, forever, with no lens fighting the ML.

This is the verification layer the whole product's credibility rests on, and it
falls out of the model rather than being bolted onto it.

## 7. Target schema deltas — additive, grounded

Each is additive over the cited current table; none rewrites the substrate.

1. **`document_collections` (047):** add a scope flag (`is_scope boolean` or a
   `kind = 'scope'`) marking a collection as a lens. Ordinary collections
   unchanged.
2. **Projection dimension:** add a nullable `lens_id` to the resolution layer
   (`entity_link` / `canonical` participation), `null` = `global`. Resolution
   runs *per lens* and is materialized; drop any notion of a one-scope-per-doc
   partition — lenses overlap.
3. **`allusion` edge** — a typed cross-lens link table `(from_mention/entity,
   to_canonical, lens_from, lens_to, actor)`; never an identity equation.
4. **Substrate correction table** — `entity_mention_correction (document_id,
   char_start, char_end, action, corrected_type, corrected_span,
   original_type, reviewer, rationale, created_at)` with RLS mirroring document
   scope; re-applied by the annotate pipeline.
5. **Per-lens projection + summary rollups** — materialized like `document_stats`
   / `lexeme_stats`, refreshed by a job; includes a GraphRAG-style community
   summary per lens for hyper-context.
6. **Genre axis on `document_classification`** — fiction/non-fiction (+ finer)
   feeding the per-entity-type resolution policy (fictional types → work lens;
   real-world types → global-preferred).

All of this respects the docs-in-sync gate (DATABASE.md must document new
tables) and the version-consistency and RLS test discipline already in place.

## 8. Delivery roadmap

Phased so each phase ships value and nothing waits on the whole.

- **P0 — Substrate corrections (verification made real).** The correction table
  + pipeline re-apply + GraphQL mutations + MCP tools + audit. Delivers
  PURPOSE.md's "verification decides" immediately, independent of lenses.
  *(This is the smallest end-to-end slice and the natural first epic.)*
- **P1 — Lenses from collections.** The scope flag + a document's default lens +
  genre routing. No projection change yet; establishes the scope vocabulary.
- **P2 — Projection engine.** `project(mention, lens)` as a materialized,
  refreshable view; `global` + one scope lens; local-first resolution;
  allusion edges. The heart of the model.
- **P3 — Hyper-context rollups.** Per-lens community summaries (GraphRAG-style);
  relationship discovery ("friend-of-a-friend," a stated PURPOSE.md goal) over
  the projected graph; composable lens queries in the API and MCP.

Each phase carries the full alignment bar doc-ray already enforces: TDD
(red-first), RLS tests, the static gates, DATABASE.md/CHANGELOG updates, and —
where a phase adds capability — consistent exposure across GraphQL, the webapp
reader, and MCP, with examples.

## 9. What this explicitly does not change

- The substrate's existing identities: `lexemes` stays the global vocabulary
  registry; raw `entity_mention` stays the NER record.
- Tenancy and RLS: lenses sit *inside* a tenant; a lens never crosses tenant
  isolation.
- "Compose outside the library": projections and summaries are curation of what
  the library holds; they never write generated documents back in.
- Human authority over durable truth: promoting a collection to a lens, drawing
  an allusion, and confirming a projection are review actions, not autonomous
  writes.

## 10. Risks and open questions

- **Projection materialization cost** at 10k docs × active lenses. Mitigation:
  only *designated* lenses project; refresh incrementally like the rollups;
  bound lenses-per-document. *Open:* incremental vs. full re-projection policy.
- **Default-lens ambiguity** for a document in several lenses. *Open:* is the
  default declared per document, or inferred from genre + the smallest
  containing lens?
- **Nested lenses** (a franchise → a film; Middle-earth → the First Age).
  Design allows `parent_lens_id`; resolution would be inner→outer→global. *Open:*
  ship flat first, or model hierarchy from the start?
- **Confidence for targeting corrections.** `entity_mention` stores no
  confidence today; polishing wants to rank the uncertain first. *Open:* persist
  the model score during annotate, or rank heuristically?
- **Cross-lens identity for real entities inside fiction.** The per-type policy
  (real types → global) needs the genre classifier to be reliable. *Open:*
  abstain-and-review when genre is uncertain, per PALS's Law.

## 11. Definition of done (per phase)

A phase is complete only when implementation, tests (including the RLS and the
substrate-immutability invariants), documentation (architecture, DATABASE.md,
usage, CHANGELOG, migration guidance), and — for capability phases — the API,
webapp, and MCP surfaces are aligned, validated, and usable end to end, with
examples. This is the standard the codebase already holds itself to; SLP does
not get an exemption.

---

## Appendix — one-line summary

**Keep an immutable, complete substrate of facts; make every entity identity a
named, derived, rebuildable projection through a lens; let collections name the
lenses and humans confirm the projections.** Completeness comes from the
substrate, reuse from sharing it, global-and-local from the lenses, and
hyper-context from composing them — the same lesson Datomic, RDF named graphs,
Wikidata, FRBR, WordNet, and GraphRAG each teach, applied to the entity fabric
PURPOSE.md already declares to be the point.

---

[← root README](../../README.md) · [PURPOSE.md](../../PURPOSE.md) · [CLAUDE.md](../../CLAUDE.md)
