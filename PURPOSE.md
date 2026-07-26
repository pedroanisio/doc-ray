---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code — revised from an OpenAI GPT-5 via Codex draft; Today/Becoming boundary reconciled to shipped state (entity-fabric Blocks A–F)"
  date: "2026-07-26"
status: current
---

# Doc-Ray

## Why We Built This

We believe a library should be alive: able to remember what it holds,
show where each idea came from, and help people or organizations build new work
without losing the thread back to the source.

Most knowledge systems make a false choice. They either preserve documents as
static files that are hard to recombine, or they generate fluent answers that
are hard to verify. For individuals, that traps hard concepts in scattered
notes and prior art. For organizations, it turns institutional knowledge into
tribal memory, duplicated research, and AI output no one can safely approve.

Doc-Ray exists to close that gap. Its connective mechanism is entities: the same
person, place, date, or organization, recognized and resolved to one shared
identity, threads scattered mentions across the whole corpus into a single
connected fabric of concepts. On that fabric, prior work inspires new work, and
creative generation stays accountable to its sources — a living library,
knowledge base, and document-composition workspace for personal creation and
governed enterprise work.

---

## How We Approach This

- **Source before synthesis**: Claims, summaries, and compositions must be traceable to the documents, spans, entities, or decisions that support them.
- **Entities are the connective tissue**: Named-entity recognition and AI-curated canonicalization are how scattered mentions of the same person, country, date, or organization become one concept that links the entire library — and the foundation for relationship (entity-to-entity, "friend-of-a-friend") discovery over the resulting graph. Connecting a large body of concepts *through the entities themselves* is a first-class goal, not a side effect.
- **AI proposes, verification decides**: Entity resolution and enrichment may be curated by AI providers, but their output is untrusted by default. Low-confidence resolutions abstain and route to review rather than assert a guess. Verification is a design layer, not an afterthought.
- **Living structure over passive storage**: A document should become searchable, linkable, annotatable knowledge, not just a file in a bucket.
- **Curate the library; compose outside it**: Curation shapes what the library already holds — collections, cataloging, titles, descriptions, identities. Composition produces *standalone* documents that are never written into the library directly; they re-enter, if at all, only through the normal ingest path, like any other source.
- **Human direction over autonomous drift**: The system — and AI agents acting within it — may suggest, enrich, curate, and compose, but durable truth, deletion, publication, shared identities, and catalog structure pass through explicit human or policy review.
- **Personal flow and organizational control**: The same corpus should support solo exploration and business needs for access, audit, approval, and continuity.
- **Hard concepts deserve faithful handling**: We preserve provenance, language structure, entities, and uncertainty so complex material can be recombined without flattening it into vague paraphrase.
- **Creativity with guardrails**: Generative tools are useful when they explore combinations and drafts; they are unsafe when they become unverified authorities.

---

## What It Does

The list below separates what is **shipped and verifiable today** from what
Doc-Ray is **becoming**. This boundary is deliberate: a document that carries the
disclaimer above must not present intent as fact.

### Today (shipped and verifiable)

- Ingests documents into a queryable corpus while preserving original files and checksums (content-addressed).
- Extracts text, layout, metadata, sentences, and a multi-layer linguistic annotation graph.
- **Recognizes named entities — people, countries and places, dates, organizations — and canonicalizes them, curated by AI providers (Anthropic or a local model) under mandatory verification**, so mentions resolve to shared identities and the *same* entity connects documents and concepts across the whole corpus.
- **Resolves identity within named *lenses* (Substrate·Lens·Projection)**: classifies a document's genre, then projects each mention to an entity *inside its own work's lens* or against the shared global scope — so fiction and non-fiction coexist (a novel's characters stay local to the work; the real Paris stays shared) with no lossy merge. Every machine projection is a proposal until a human confirms it.
- **Discovers relationships over the entity graph**: entity-to-entity co-occurrence within a lens, bounded several-hops ("friend-of-a-friend") traversal, a per-lens entity-landscape rollup, and typed cross-lens *allusion* edges (a fictional entity *alludes to* a real one) — links that connect concepts without ever asserting identity.
- **Retrieves by meaning, not only keywords**: learned semantic sentence search and semantically-ranked briefing passages, alongside full-text and linguistic search.
- Curates the library: collections, editable titles/descriptions/tags, and lifecycle, including an AI-suggest → human-decide metadata workflow.
- Exposes the corpus for search and analysis over a GraphQL API, a browser webapp, and an MCP server for AI agents — read-only by default, with opt-in write tools (reprocessing and curation) behind a deployment flag.
- Supports governed organizational use: identity, access control (RBAC/ABAC, tenant-scoped RLS), SSO/SCIM, audit, backup, and operator workflows.

### Becoming (intent — not yet built)

- **Document Composer**: generating standalone documents that draw from prior art, combine concepts, and keep every generated claim grounded in — and cited to — source evidence. (Composed documents re-enter the library, if at all, only through the normal ingest path.)
- **Inspiration from prior art**: building on shipped semantic retrieval to surface *whole prior works* relevant to a new composition by meaning — recommending, not just searching.
- **Per-lens narrative summaries**: generated, review-gated overviews of a lens's entity landscape — model output that ships as a citable proposal, never asserted as fact.
- **Confidence-guided review**: ranking uncertain machine resolutions so human verification targets the least-certain first.
- **Agent-driven curation**: AI agents that propose reorganizations of the library (catalog, collections, metadata) through a governed, human-or-policy-approved write path.

### What This Is Not

This project does **not**:

- Replace human judgment with generated certainty.
- Treat a pile of documents as a finished knowledge base.
- Optimize for untraceable chat-style answers.
- Assume every user is either solo or enterprise; both use cases matter.
- Hide provenance, uncertainty, or curation decisions for the sake of fluency.

---

## Who This Is For

- **Individual researchers, authors, and builders**: People who need a personal living library for prior art, hard concepts, and grounded synthesis.
- **Organizations with serious document collections**: Teams that need governed ingestion, curation, access control, auditability, backups, and continuity.
- **Knowledge, product, legal, research, and compliance teams**: Groups that need reusable institutional knowledge and checkable AI-assisted outputs.
- **AI agents acting under governance**: First-class but non-sovereign actors that read, resolve, curate, and (in time) compose — always proposing durable changes for human or policy approval, never bypassing review.
