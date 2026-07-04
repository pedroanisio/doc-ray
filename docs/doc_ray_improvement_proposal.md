---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI Codex"
  date: "2026-07-04"
---

# Doc-Ray Improvement Proposal

> **Superseded as a backlog source (2026-07-04).** The 10 backlog-shaped
> candidates in §5 (`BL-016`–`BL-025`) have been merged into the canonical
> backlog — [`docs/backlog.yml`](backlog.yml) / [`docs/BACKLOG.md`](BACKLOG.md)
> — which is now the single source of truth for their status, dependencies,
> and evidence. This document is retained for its narrative analysis
> (executive summary, gap analysis, architectural/clean-code standards, and
> phased implementation order below) but no longer tracks item status; edit
> the canonical backlog instead.

## 1. Executive Summary

The GraphQL corpus source establishes Doc-Ray as a domain system for composing
NLP work from raw language into auditable structure: extraction, segmentation,
lexical analysis, representation, modeling, evaluation, and maintenance. In this
proposal, "GraphQL source" means the source corpus and live schema inspected
through GraphQL; "GraphQL API" means Doc-Ray's Strawberry service. The current
codebase already has a strong spine for the domain mission:
content-addressed ingest, stand-off spans, normalized document metadata,
layer-aware annotation runs, worker-backed mutations, corpus health checks, a
GraphQL API, and a browser for reading and curation.

The main gap is not database ambition; it is product/API completeness around the
database. The schema and pipeline can persist or model tokens, morphology,
chunks, dependencies, entity mentions, shallow coreference, canonical links,
lexemes, and embeddings. GraphQL and the browser expose only a small subset of
that model: documents, sentences, lexemes, entity mentions/frequencies, jobs,
and seed suggestions. That makes Doc-Ray look like a sentence/lexeme browser
even though its own data model is closer to a full NLP annotation platform.

The highest-value improvement is to make the platform's actual domain model
visible, validated, and operational. The recommended order is:

1. Publish a capability matrix that distinguishes schema support, production
   writers, GraphQL queries, browser views, tests, and provenance for every NLP
   layer.
2. Add a layer-aware read model and GraphQL slices for annotation runs, tokens,
   syntax, canonical entity links, review residue, and embeddings.
3. Close provenance and validation gaps in annotation output, especially the
   spaCy multi-layer path.
4. Collapse duplicated SQL between GraphQL and the Flask browser into shared read
   surfaces or parity-checked projections.
5. Treat corpus quality, database health, and model-output verification as
   first-class operator surfaces rather than scattered CLIs and tests.

## 2. Source-Grounded Domain Understanding

The source-derived NLP brief frames NLP as a full composition problem, not a
model call. It defines the work as turning raw language into usable software
outcomes through data curation, linguistic analysis, representation, modeling,
evaluation, and maintenance
([docs/nlp_composition_mastery.md:15](nlp_composition_mastery.md:15)). Its core
thesis is that language has form, meaning, and use, and machines need that
language converted into machine-operable structure
([docs/nlp_composition_mastery.md:28](nlp_composition_mastery.md:28)).

That maps well onto Doc-Ray's intended database model. The database
specification says Doc-Ray is a "document store and NLP annotation platform"
that ingests documents and decomposes them into a normalized,
provenance-tracked relational model
([docs/DATABASE_SPEC.md:10](DATABASE_SPEC.md:10)). It names three central
question classes: bibliographic retrieval, structural/linguistic decomposition,
and provenance/reproducibility
([docs/DATABASE_SPEC.md:16](DATABASE_SPEC.md:16)). It also declares the database
the single source of truth for metadata, structure, annotations, and audit trail
([docs/DATABASE_SPEC.md:26](DATABASE_SPEC.md:26)).

The source implies these design requirements for Doc-Ray:

- Raw text, offsets, spans, and original bytes must remain recoverable because
  NLP outputs need to map back to inspectable source text.
- Preprocessing, segmentation, tokenization, and normalization are design
  choices that must be recorded, not hidden as implementation details.
- Representations should progress from simple exact search through counts,
  co-occurrence, embeddings, and contextual models only where the task justifies
  the tradeoff
  ([docs/nlp_composition_mastery.md:203](nlp_composition_mastery.md:203)).
- Model output is a hypothesis that needs validation, evaluation, and drift
  monitoring.
- A language pipeline is incomplete if it cannot explain what was run, by which
  tool/version, with what input/output hashes, and how the result can be checked.

Doc-Ray's architecture is directionally aligned with that domain. The important
next step is to make the alignment explicit and testable at the API,
validation, documentation, and operational layers.

## 3. Codebase Assessment

### Strengths

Doc-Ray has a strong normalized persistence model. The database spec requires
stand-off annotation through shared spans, layer-aware annotation runs, and
append-only provenance events
([docs/DATABASE_SPEC.md:35](DATABASE_SPEC.md:35)). The current spaCy annotator
uses those concepts directly: it registers token/entity/chunk spans, writes
tokens, token features, dependencies, entity mentions, chunks, coreference, and
a lexeme projection
([src/doc_ray/adapters/annotation_spacy.py:1](/home/admin/codebases/doc-ray/src/doc_ray/adapters/annotation_spacy.py:1)).

The GraphQL API is thin and mostly well factored. Query resolvers clamp pages and
delegate to `persistence_read`, rather than embedding SQL in resolver functions
([src/doc_ray/entrypoints/api/query.py:1](/home/admin/codebases/doc-ray/src/doc_ray/entrypoints/api/query.py:1)).
The document type uses lazy batched relationships for authors, tags, metadata,
sentence count, token count, entities, sentences, and provenance
([src/doc_ray/entrypoints/api/types.py:128](/home/admin/codebases/doc-ray/src/doc_ray/entrypoints/api/types.py:128)).

The mutation/workflow model is pragmatic. Upload, annotate, canonicalize,
backup, restore, and seeding actions enqueue worker jobs. The worker handles
ingest materialization, annotation, canonicalization, seed mining, canonical
seeding, backup, and restore through a central dispatch table
([src/doc_ray/application/jobs.py:75](/home/admin/codebases/doc-ray/src/doc_ray/application/jobs.py:75)).

The canonicalization design is conservative in the right way. The docs require
the frontier model to choose from candidates, declare a new entity, or abstain,
never inventing an identity
([docs/canonicalization_pipeline.md:4](canonicalization_pipeline.md:4)).
The Claude resolver implements that as a strict constrained-choice tool and
coerces malformed output to abstention, so bad model output routes to review
instead of being committed
([src/doc_ray/adapters/resolver_claude.py:1](/home/admin/codebases/doc-ray/src/doc_ray/adapters/resolver_claude.py:1)).

The project already has the beginnings of operator observability. Corpus health
captures totals for documents, sentences, tokens, mentions, links, canonical
entries, lexemes, and embeddings, then flags empty extracted documents, missing
language, missing entity mentions, failed jobs, stalled jobs, and missing
embeddings
([src/doc_ray/application/corpus_health.py:1](/home/admin/codebases/doc-ray/src/doc_ray/application/corpus_health.py:1)).

### Weaknesses

The public GraphQL API model is much narrower than the database and annotator
model.
The query root exposes documents, document lookup, sentence search, full-text
search, lexemes, entities, entity frequency, jobs, and seed suggestions
([src/doc_ray/entrypoints/api/query.py:28](/home/admin/codebases/doc-ray/src/doc_ray/entrypoints/api/query.py:28)).
It does not expose first-class queries for annotation runs, tokens, token
features, dependencies, chunks, coreference chains, entity links, canonical
entries/forms, embeddings, or validation/evaluation results.

Documentation currently overstates the implemented surface in places. The
database spec says migrations 008-012 cover the remaining NLP stack and that
each layer is live and exercised by tests
([docs/DATABASE_SPEC.md:213](DATABASE_SPEC.md:213)). That is true for schema and
fixture-level coverage, but production annotation currently materializes only a
subset: spaCy tokens, morphology, chunks, dependency, NER, shallow string-match
coreference, and lexeme projection
([src/doc_ray/adapters/annotation_spacy.py:1](/home/admin/codebases/doc-ray/src/doc_ray/adapters/annotation_spacy.py:1)).
Layers such as subword, constituent, SRL, WSD, AMR, discourse, temporal,
sentiment, and embeddings appear as schema capacity or health counters, not as a
complete production pipeline.

Provenance is uneven. Ingest and some annotation paths record provenance, and
the database spec requires append-only provenance for each processing step
([docs/DATABASE_SPEC.md:123](DATABASE_SPEC.md:123)). The spaCy multi-layer writer
persists annotation rows and the lexeme projection but does not insert a
`document_provenance` event for the run
([src/doc_ray/adapters/annotation_spacy.py:267](/home/admin/codebases/doc-ray/src/doc_ray/adapters/annotation_spacy.py:267)).
That weakens reproducibility for one of the highest-value transformations in the
system.

The read abstraction is incomplete. `persistence_read.py` says it is the single
source of truth for GraphQL, the Flask browser, and export reads
([src/doc_ray/adapters/persistence_read.py:1](/home/admin/codebases/doc-ray/src/doc_ray/adapters/persistence_read.py:1)).
But `webapp/app.py` still reads the corpus database directly with inline SQL for
document lists, document details, POS stats, lexemes, KWIC, search, jobs,
seeding, and backups
([webapp/app.py:174](/home/admin/codebases/doc-ray/webapp/app.py:174)).
The user manual confirms this split: reads go straight to the corpus database,
while writes go through GraphQL
([docs/webapp_user_manual.md:19](webapp_user_manual.md:19)).

The repository port has drifted behind the adapter. The `ReadRepository`
protocol lacks newer `document_id` filters now used by GraphQL for lexemes,
KWIC, and sentence search
([src/doc_ray/ports/repository.py:101](/home/admin/codebases/doc-ray/src/doc_ray/ports/repository.py:101);
[src/doc_ray/entrypoints/api/query.py:90](/home/admin/codebases/doc-ray/src/doc_ray/entrypoints/api/query.py:90)).
This makes the protocol less useful as a contract.

Language support is implicit and narrow. The spaCy adapter maps only English and
Portuguese, and unknown languages fall back to English
([src/doc_ray/adapters/annotation_spacy.py:35](/home/admin/codebases/doc-ray/src/doc_ray/adapters/annotation_spacy.py:35)).
That is acceptable for a bootstrap path, but it should be a visible capability
and health signal, not a quiet default.

Testing is strong around the exposed API and some persisted layers, but not yet
aligned to the full domain claim. The backlog itself identifies remaining work
for the unmaterialized NLP layers, output verification, and completing the
GraphQL adapter
([docs/BACKLOG.md:45](BACKLOG.md:45)).

## 4. Gap Analysis

| Domain need | Current support | Gap |
| --- | --- | --- |
| Full NLP composition pipeline | Ingest, segmentation, spaCy annotation, canonicalization jobs, health checks | No first-class task/dataset/evaluation model; maintenance and drift are mostly implicit |
| Auditable transformations | Database spec and ingest provenance are strong | spaCy annotation path lacks a provenance event with model/tool/version/counts |
| Layer-aware inspection | Schema supports many NLP layers; spaCy writes several | GraphQL/browser expose only a narrow subset |
| Representation progression | Exact search, lexemes, FTS, embedding table/health counter | No API/UI for embeddings, similarity, co-occurrence, or representation comparison |
| Human-in-the-loop identity resolution | Seeding screen, constrained resolver, review residue path | Review/canonical entity surfaces are not complete in GraphQL/browser |
| Single source of read truth | `persistence_read` exists and GraphQL uses it | Flask browser still duplicates SQL directly |
| Language diversity | English and Portuguese spaCy model mapping | Unknown-language fallback is silent; no per-language capability registry |
| Validation and evaluation | Tests and corpus health exist | No generic annotation validation pass or evaluation-run artifact |
| Observability | `/health`, `/readyz`, corpus health, DB health/analyzer work in tree | Health surfaces are not integrated into GraphQL/browser/operator workflows |
| Documentation alignment | Strong database and user docs | Need capability matrix to separate implemented, schema-only, API-exposed, and UI-exposed features |

The gaps cluster into two groups. First, visibility gaps: the database can hold
more than clients can query or inspect. Second, trust gaps: some high-value
derived artifacts are not yet backed by provenance, validation, evaluation, or
drift checks. The implementation plan below handles trust before broadening the
public surface, so the API does not amplify unvalidated layers.

## 5. Proposed Improvements / Candidate Backlog Items

Each suggestion below keeps the `docs/backlog.yml` shape so accepted work can be
copied into the backlog with minimal translation. Before the backlog-shaped
items, this section defines the architectural quality and clean-code standards
used to judge each proposal. These standards are derived from the inspected
codebase practices, not from a generic style guide. `PURPOSE.md` was not present
in this checkout, so this section is grounded in `CLAUDE.md`, `pyproject.toml`,
`engineering_decisions.yml`, `docs/architecture.md`, and the current package
structure.

The source mapping is:

- `docs/architecture.md` defines the hexagonal ports/adapters boundary, package
  layering, worker-backed GraphQL write path, and browser/package separation.
- `docs/DATABASE_SPEC.md` defines the database as the single source of truth,
  the 4NF/provenance model, content addressing, ULID identity, and layer-aware
  NLP persistence expectations.
- `engineering_decisions.yml` records the governing decisions for GraphQL,
  shared read repositories, background jobs, GraphQL safety limits, RLS,
  `ruff`, strict `mypy`, coverage, and ADR traceability.
- `pyproject.toml` supplies executable quality gates: `ruff` line length and
  lint families, strict `mypy` over `src/doc_ray`, and coverage
  `fail_under = 90`.
- `CLAUDE.md` adds project-wide AI-agent constraints: PALS verification,
  verifiable references, Markdown disclaimer frontmatter, TDD discipline, and
  no placeholder completion claims.

### Architectural Quality Standard

A proposed change is architecturally acceptable when it preserves these
properties:

- **Hexagonal boundary:** driving adapters such as GraphQL and CLI code delegate
  to application/use-case code and ports; SQL does not move into resolvers.
- **Single source of truth:** relational tables and content-addressed blobs hold
  durable facts; generated views/projections are either derived or explicitly
  parity-tested.
- **Layer-aware NLP model:** annotation facts are scoped by document, layer,
  tool, version, and stand-off span, not stored as opaque JSON.
- **Provenance and reproducibility:** every irreversible transformation records
  tool identity, version, input/output identity, and parameters.
- **PALS verification:** ML/LLM output is untrusted by default and must pass
  schema/span/consistency validation before persistence.
- **Bounded API surface:** GraphQL list fields are paginated, complexity-limited,
  and backed by read adapters or shared query definitions.
- **One write path:** user-facing writes go through API mutations and durable job
  execution, not direct browser SQL.
- **Observable operations:** long-running ingestion, annotation, canonicalization,
  backup, and restore work must leave inspectable job and health state.
- **Security boundaries:** RLS and admin/operator surfaces must state their trust
  assumptions; diagnostics that expose operational details need admin-safe access.
- **Decision traceability:** significant architectural shifts update or reference
  `engineering_decisions.yml`, with tradeoffs and validation checks.

### Clean Code Standard

A proposed change is clean enough for this codebase when it satisfies these
local practices:

- Python package code stays under `src/doc_ray`; `webapp/` and `scripts/` remain
  outside the package boundary.
- `ruff` rules stay green: 100-character line length, imports sorted, modern
  Python idioms, bugbear checks, simplification checks, Google-style docstrings,
  annotations, and mccabe complexity no higher than 12 where enforced.
- `mypy` remains strict for `src/doc_ray`; new public code is typed rather than
  inferred by convention.
- Tests are deterministic, realistic, and close to the risk: pure validators get
  unit tests; repository/API/workflow changes get integration tests.
- The coverage gate remains `fail_under = 90`; tests should raise meaningful
  package coverage rather than bypass the gate.
- Library code uses typed/domain errors where possible; applications convert
  those into user-safe messages.
- Dependencies are justified by domain value and kept out of hot paths unless
  needed.
- Comments explain non-obvious invariants or trust boundaries, not line-by-line
  mechanics.
- Markdown artifacts keep the required YAML disclaimer frontmatter.
- No proposal relies on placeholders, hidden manual steps, or unverified
  generated output as a completed state.

### Backlog Item Format

Each candidate is split into two parts:

- **Backlog candidate part:** only fields accepted by
  `docs/schema/backlog.schema.json` and used by `docs/backlog.yml`.
- **Proposal analysis part:** evidence, GraphQL-source rationale, standards
  alignment, tradeoffs, and validation detail that should remain in this
  proposal or be folded into backlog `description`, `rationale`,
  `acceptance_criteria`, `references`, and `source` when accepted.

The backlog-compatible field set is `id`, `title`, `summary`, `description`,
`category`, `type`, `status`, `complexity`, `priority`, `rationale`,
`acceptance_criteria`, `dependencies`, `related_decisions`, `references`,
`owner`, `source`, and `tags`. The candidate items are not inserted into the
backlog here; the next implementation step would be to add accepted items to
`docs/backlog.yml` and increment `metadata.total_items`.

#### BL-016: NLP Capability Matrix and Drift Guard

**Backlog candidate:**

- **id:** `BL-016`
- **title:** NLP capability matrix and drift guard
- **summary:** Map each NLP layer to schema, writer, provenance, read adapter,
  GraphQL field, browser view, health signal, and tests.
- **category:** `docs`
- **type:** `infra`
- **status:** `ready`
- **complexity:** `S`
- **priority:** `high`
- **owner:** `shared`
- **rationale:** Documentation and schema breadth can imply production-ready
  support that is not exposed or populated everywhere.
- **description:** Create `docs/nlp_capability_matrix.md` and a field/path
  existence check. The matrix must distinguish schema-only support from
  writer-backed, API-exposed, UI-exposed, health-checked, and tested support.
- **acceptance_criteria:**
  - "Every documented NLP layer has a row with status and evidence."
  - "Rows marked API-exposed resolve to live GraphQL fields."
  - "Rows marked writer-backed cite a production code path and a test."
- **related_decisions:** `D-001`, `D-002`
- **references:** `docs/DATABASE_SPEC.md`, `docs/nlp_composition_mastery.md`,
  `src/doc_ray/entrypoints/api/query.py`,
  `src/doc_ray/adapters/annotation_spacy.py`
- **source:** Session finding from GraphQL-source/codebase comparison:
  database/schema capability exceeds public API and browser exposure.
- **tags:** `docs`, `drift`, `graphql`, `nlp`

**Proposal analysis:**

- **standards_alignment:** Satisfies architectural traceability and clean documentation
  frontmatter requirements.
- **problem:** The documented NLP scope and the implemented/public surfaces can
  drift, making it unclear whether a layer is schema-only, writer-backed,
  queryable, tested, or operator-visible.
- **codebase_evidence:** `docs/DATABASE_SPEC.md` describes a normalized NLP
  annotation platform, while `query.py` and `types.py` expose only the document,
  sentence, lexeme, entity, job, and seed-suggestion subset.
- **supporting_graphql_knowledge:** The GraphQL source corpus frames NLP as a
  staged workflow across data quality, preprocessing, representation, modeling,
  and evaluation; the current GraphQL API itself exposes only part of that
  domain state.
- **proposed_change:** Add the capability matrix plus a lightweight CI guard
  that checks declared API-exposed and writer-backed rows against live paths.
- **expected_benefit:** Gives maintainers and agents a single map for safe
  extension, reducing silent overclaims and duplicate discovery work.
- **implementation_complexity:** `S`
- **risks_or_tradeoffs:** The matrix can become another stale document unless
  rows are tied to mechanical checks and reviewed with schema/API changes.
- **suggested_validation:** Run the field/path existence guard in CI and require
  rows marked writer-backed to cite at least one production path and one test.

#### BL-017: Layer-Aware GraphQL Read Model

**Backlog candidate:**

- **id:** `BL-017`
- **title:** Layer-aware GraphQL read model
- **summary:** Expose annotation runs, tokens, morphology, syntax, coreference,
  canonical links, and later embeddings through bounded GraphQL fields.
- **category:** `api`
- **type:** `feature`
- **status:** `parked`
- **complexity:** `L`
- **priority:** `high`
- **owner:** `concurrent`
- **rationale:** The GraphQL API currently exposes a narrow document/sentence/
  lexeme/entity slice of a richer relational NLP graph.
- **description:** Extend the Strawberry schema through read-adapter functions:
  `annotationRuns`, `tokens`, `syntax`, `corefChains`, `entityLinks`,
  `canonical`, `canonicalForms`, and only later embedding/similarity fields.
- **acceptance_criteria:**
  - "New list fields are bounded and covered by GraphQL tests."
  - "Resolvers contain no inline SQL."
  - "Query complexity/depth limits cover the new nested fields."
- **dependencies:** `BL-016`, `BL-009`
- **related_decisions:** `D-001`, `D-002`, `D-008`
- **references:** `src/doc_ray/entrypoints/api/query.py`,
  `src/doc_ray/entrypoints/api/types.py`, `src/doc_ray/adapters/persistence_read.py`
- **source:** Session finding: API surface does not expose persisted NLP layers.
- **tags:** `graphql`, `api`, `nlp`, `read-model`

**Proposal analysis:**

- **standards_alignment:** Preserves hexagonal boundaries, bounded API access, and shared
  read definitions.
- **problem:** The API does not let clients inspect most persisted NLP layers,
  forcing direct SQL or undocumented assumptions for token, syntax,
  coreference, canonical, and embedding workflows.
- **codebase_evidence:** `types.py` lacks token, token-feature, dependency,
  chunk, coreference, entity-link, canonical-form, and embedding GraphQL types
  even though the schema/writer paths persist several of those layers.
- **supporting_graphql_knowledge:** The source material emphasizes tokenization,
  linguistic features, representations, entities, model outputs, and evaluation
  as connected NLP facts that should remain inspectable.
- **proposed_change:** Add bounded read-adapter-backed GraphQL types and fields
  for the persisted layers, with embeddings and similarity postponed until
  baseline layer reads are stable.
- **expected_benefit:** Turns Doc-Ray from a document browser into a usable
  graph-facing NLP platform without bypassing repository boundaries.
- **implementation_complexity:** `L`
- **risks_or_tradeoffs:** Rich nested queries can create performance and
  authorization risks unless pagination, depth, and complexity controls are
  enforced from the start.
- **suggested_validation:** Add GraphQL integration tests for each new field,
  resolver tests proving no inline SQL, and negative tests for query limits.

#### BL-018: spaCy Annotation Provenance

**Backlog candidate:**

- **id:** `BL-018`
- **title:** spaCy annotation provenance
- **summary:** Record provenance events for spaCy multi-layer annotation runs.
- **category:** `nlp`
- **type:** `infra`
- **status:** `ready`
- **complexity:** `M`
- **priority:** `high`
- **owner:** `shared`
- **rationale:** Annotation rows are high-value derived facts; without an audit
  event, the run is not fully reproducible.
- **description:** Insert an `annotated` provenance event in the same transaction
  that writes spaCy tokens, features, dependencies, entities, chunks, coref, and
  lexeme projections. Include tool, version, language, counts, and replacement
  semantics.
- **acceptance_criteria:**
  - "A successful annotation run creates a provenance event."
  - "The event records language, tool/version, and per-layer counts."
  - "The run is never visible without its provenance event."
- **references:** `docs/DATABASE_SPEC.md`,
  `src/doc_ray/adapters/annotation_spacy.py`,
  `src/doc_ray/entrypoints/api/types.py`
- **source:** Session finding: `SpacyAnnotator._write` persists layers without
  `document_provenance`.
- **tags:** `provenance`, `spacy`, `nlp`, `reproducibility`

**Proposal analysis:**

- **standards_alignment:** Enforces provenance/reproducibility and transaction integrity.
- **problem:** Derived annotation facts can be stored without a durable audit
  event that identifies the annotator, model, language, counts, and replacement
  semantics.
- **codebase_evidence:** `SpacyAnnotator._write` persists tokens, features,
  dependencies, entities, chunks, coreference, and lexeme projections, but the
  inspected path did not write `document_provenance` for the annotation run.
- **supporting_graphql_knowledge:** The source corpus treats preprocessing and
  representation as model-dependent transformations, so downstream use needs to
  know which tool/version produced the facts.
- **proposed_change:** Insert an annotation provenance event in the same
  transaction as layer writes and include per-layer counts plus model metadata.
- **expected_benefit:** Makes annotation output reproducible, auditable, and
  safer to compare across reruns or model upgrades.
- **implementation_complexity:** `M`
- **risks_or_tradeoffs:** Reruns need clear replacement semantics to avoid
  duplicate or misleading provenance events.
- **suggested_validation:** Add an integration test where annotation success
  creates exactly one matching provenance event and failure leaves no partial
  visible run.

#### BL-019: Annotation Output Validation

**Backlog candidate:**

- **id:** `BL-019`
- **title:** Annotation output validation
- **summary:** Validate structural NLP outputs before marking annotation healthy.
- **category:** `nlp`
- **type:** `infra`
- **status:** `ready`
- **complexity:** `M`
- **priority:** `high`
- **owner:** `shared`
- **rationale:** NLP outputs are hypotheses; invalid spans, dependencies, or
  projections should not silently enter the corpus.
- **description:** Add pure validators for span bounds, token ordinals, sentence
  references, dependency heads, entity/chunk/coref spans, lexeme projections, and
  language/model support. Record validation status in job output and provenance.
- **acceptance_criteria:**
  - "Invalid spans or cross-run dependency heads fail validation."
  - "Validation failures are visible in job/provenance output."
  - "Pure validator tests and at least one integration failure test exist."
- **dependencies:** `BL-007`, `BL-018`
- **references:** `CLAUDE.md`, `docs/BACKLOG.md`,
  `src/doc_ray/adapters/resolver_claude.py`,
  `src/doc_ray/adapters/annotation_spacy.py`
- **source:** Session finding plus PALS requirement: deterministic annotators
  still need structural validation.
- **tags:** `pals-law`, `validation`, `nlp`, `quality-gate`

**Proposal analysis:**

- **standards_alignment:** Applies PALS verification, typed validation errors, and
  deterministic tests.
- **problem:** Structural NLP output can enter storage even when spans,
  ordinals, dependency heads, or projections are inconsistent.
- **codebase_evidence:** PALS-style validation exists around the Claude
  canonical resolver, but the spaCy annotation path has no equivalent pure
  validation layer for deterministic NLP artifacts.
- **supporting_graphql_knowledge:** The source corpus stresses noisy inputs,
  language variation, and model/evaluation discipline; persisted annotations
  should be treated as hypotheses until checked.
- **proposed_change:** Add pure validators before persistence or healthy job
  completion, then record validation status in job output and provenance.
- **expected_benefit:** Prevents corrupt annotation graphs from becoming
  durable facts and gives operators actionable failure messages.
- **implementation_complexity:** `M`
- **risks_or_tradeoffs:** Over-strict validation can reject useful partial
  annotations unless failure modes distinguish fatal errors from warnings.
- **suggested_validation:** Add unit tests for malformed spans, invalid heads,
  and projection mismatch, plus one end-to-end job failure fixture.

#### BL-020: Shared Read Surface or Parity Tests

**Backlog candidate:**

- **id:** `BL-020`
- **title:** Shared read surface or parity tests
- **summary:** Eliminate or detect SQL drift between GraphQL, browser, and export
  reads.
- **category:** `api`
- **type:** `tech-debt`
- **status:** `ready`
- **complexity:** `M`
- **priority:** `high`
- **owner:** `shared`
- **rationale:** `persistence_read.py` claims to be the read source of truth, but
  the Flask browser still carries direct SQL for overlapping views.
- **description:** Either extract shared SQL builders/sync+async read wrappers or
  add parity tests that compare browser SQL against `persistence_read` for
  documents, lexemes, KWIC, and search. Update `ReadRepository` signatures to
  match adapter filters.
- **acceptance_criteria:**
  - "Document, lexeme, KWIC, and search read shapes have parity tests or shared code."
  - "`ReadRepository` matches implemented adapter signatures."
  - "No new GraphQL resolver embeds SQL."
- **related_decisions:** `D-002`
- **references:** `src/doc_ray/adapters/persistence_read.py`,
  `src/doc_ray/ports/repository.py`, `webapp/app.py`,
  `engineering_decisions.yml`
- **source:** Session finding: read abstraction contract and webapp SQL diverge.
- **tags:** `read-model`, `sql-drift`, `webapp`, `graphql`

**Proposal analysis:**

- **standards_alignment:** Enforces single source of truth, adapter boundaries, and
  regression tests before refactor.
- **problem:** Browser, GraphQL, and export reads can diverge because overlapping
  read logic is implemented in multiple places.
- **codebase_evidence:** `persistence_read.py` is documented as the shared read
  source, but `webapp/app.py` still contains inline SQL for document, lexeme,
  KWIC, and search flows; `ReadRepository` also lags implemented adapter
  signatures.
- **supporting_graphql_knowledge:** Domain users need the same corpus facts and
  terminology whether they inspect them through GraphQL, browser pages, or
  exports.
- **proposed_change:** Extract shared read definitions or add parity tests for
  overlapping flows, then align the repository port with implemented filters.
- **expected_benefit:** Reduces UI/API disagreement and makes future read-model
  expansion cheaper.
- **implementation_complexity:** `M`
- **risks_or_tradeoffs:** A full shared sync/async abstraction may add
  complexity; parity tests may be a better first step where extraction is noisy.
- **suggested_validation:** Compare seeded fixture outputs across GraphQL read
  adapters and browser SQL for documents, lexemes, KWIC, and search.

#### BL-021: Canonicalization and Review API Surfaces

**Backlog candidate:**

- **id:** `BL-021`
- **title:** Canonicalization and review API surfaces
- **summary:** Expose canonical registry, form index, entity links, unresolved
  mentions, and review residue through supported API/browser surfaces.
- **category:** `api`
- **type:** `feature`
- **status:** `parked`
- **complexity:** `M`
- **priority:** `high`
- **owner:** `concurrent`
- **rationale:** Canonicalization has constrained internal logic, but clients
  cannot fully inspect the registry/review state without direct SQL or job JSON.
- **description:** Add GraphQL fields for `canonical`, `canonicalSearch`,
  `canonicalForms`, `entityLinks`, `unresolvedMentions`, and
  `resolutionReview`; add browser review/canonical pages after the API stabilizes.
- **acceptance_criteria:**
  - "Canonical forms and mention links are queryable through GraphQL."
  - "Unresolved mentions and review rows are visible without inspecting job JSON."
  - "Review lifecycle tests cover approve/reject or explicitly scoped first actions."
- **dependencies:** `BL-017`
- **related_decisions:** `D-001`, `D-002`
- **references:** `docs/canonicalization_pipeline.md`,
  `src/doc_ray/adapters/canonical_store.py`, `src/doc_ray/application/jobs.py`,
  `webapp/app.py`
- **source:** Session finding: canonicalization workflow lacks first-class read
  surfaces.
- **tags:** `canonicalization`, `review`, `graphql`, `human-in-the-loop`

**Proposal analysis:**

- **standards_alignment:** Preserves constrained-choice/PALS behavior and makes review
  observable.
- **problem:** Canonicalization produces important registry, link, and review
  state that is not first-class through supported read surfaces.
- **codebase_evidence:** `jobs.py`, `canonical_store.py`, and
  `docs/canonicalization_pipeline.md` define constrained canonicalization, but
  clients must inspect job JSON or direct SQL to understand unresolved residue
  and review state.
- **supporting_graphql_knowledge:** The source corpus highlights entities,
  relationships, terminology, and workflow alignment; those become operational
  only when canonical identities and unresolved mentions are visible.
- **proposed_change:** Add GraphQL and later browser surfaces for canonical
  registry entries, form index entries, mention links, unresolved mentions, and
  review rows.
- **expected_benefit:** Enables human-in-the-loop correction and agentic review
  without weakening the constrained-choice resolver design.
- **implementation_complexity:** `M`
- **risks_or_tradeoffs:** Review actions expand product scope and may need
  authorization/audit decisions before mutation support is added.
- **suggested_validation:** Cover read lifecycle with fixtures for resolved,
  newly-created, abstained, unresolved, and review-required mentions.

#### BL-022: Annotation Language and Model Capability Registry

**Backlog candidate:**

- **id:** `BL-022`
- **title:** Annotation language and model capability registry
- **summary:** Replace silent unknown-language fallback with explicit capability
  and fallback policy.
- **category:** `nlp`
- **type:** `infra`
- **status:** `ready`
- **complexity:** `S`
- **priority:** `medium`
- **owner:** `shared`
- **rationale:** Unknown-language fallback to English can create plausible but
  low-quality annotations without an operator-visible signal.
- **description:** Add a registry of language, model package, installed status,
  supported layers, and fallback policy. Unsupported languages should fail
  clearly, warn-and-fallback with provenance, or use a configured generic
  tokenizer.
- **acceptance_criteria:**
  - "Supported and unsupported languages are tested."
  - "Fallback behavior is configurable and recorded."
  - "Capability data feeds the capability matrix or GraphQL capability query."
- **dependencies:** `BL-016`, `BL-018`
- **references:** `src/doc_ray/adapters/annotation_spacy.py`,
  `src/doc_ray/application/corpus_health.py`
- **source:** Session finding: `_MODELS` maps only English/Portuguese and
  unknowns fall back to English.
- **tags:** `language`, `spacy`, `capability`, `provenance`

**Proposal analysis:**

- **standards_alignment:** Makes quality degradation explicit and testable.
- **problem:** Unknown-language fallback can produce plausible but low-quality
  annotations without a clear operator-visible signal.
- **codebase_evidence:** `_MODELS` maps English and Portuguese and the inspected
  annotator behavior falls back to English for unknown languages.
- **supporting_graphql_knowledge:** The source corpus includes multilingual and
  cross-lingual examples, including small Arabic datasets and transfer-learning
  workflows, which makes language capability an explicit quality variable.
- **proposed_change:** Introduce a language/model capability registry with
  installed status, supported layers, and configurable fallback policy.
- **expected_benefit:** Makes annotation quality degradation transparent and
  gives operators predictable choices for unsupported languages.
- **implementation_complexity:** `S`
- **risks_or_tradeoffs:** Stricter fallback handling can interrupt existing
  ingestion flows unless a compatibility mode is provided.
- **suggested_validation:** Test supported, unsupported, and fallback language
  paths and assert that provenance records the chosen policy.

#### BL-023: Operator Health Surfaces

**Backlog candidate:**

- **id:** `BL-023`
- **title:** Operator health surfaces
- **summary:** Standardize corpus/database health output and expose admin-safe
  operator views.
- **category:** `ops`
- **type:** `feature`
- **status:** `parked`
- **complexity:** `M`
- **priority:** `medium`
- **owner:** `shared`
- **rationale:** Health checks exist, but their outputs are not yet a coherent
  operator workflow.
- **description:** Standardize health JSON schemas, expose corpus health and
  selected database health through GraphQL/admin endpoints or a browser operator
  page, and wire core checks into smoke/CI.
- **acceptance_criteria:**
  - "Corpus health has a stable JSON schema and snapshot tests."
  - "Failed jobs, stalled jobs, missing annotations, and missing embeddings are visible."
  - "Database-health details are admin-gated or explicitly kept CLI-only."
- **dependencies:** `BL-016`
- **references:** `src/doc_ray/application/corpus_health.py`,
  `src/doc_ray/application/db_health.py`,
  `src/doc_ray/application/db_analyzer/`
- **source:** Session finding: corpus health exists and database analyzer/health
  modules were present in the inspected checkout.
- **tags:** `ops`, `health`, `observability`, `admin`

**Proposal analysis:**

- **standards_alignment:** Keeps observability explicit while respecting security
  boundaries.
- **problem:** Operators cannot yet rely on one stable workflow for corpus,
  job, annotation, embedding, and database health.
- **codebase_evidence:** `corpus_health.py`, `db_health.py`, and
  `application/db_analyzer/` contain health logic, but the inspected surfaces do
  not present a coherent admin-safe health contract.
- **supporting_graphql_knowledge:** The source corpus repeatedly connects NLP
  success to data quality, noise handling, and evaluation, all of which require
  observable corpus state.
- **proposed_change:** Standardize health schemas and expose accepted checks
  through CLI, GraphQL/admin endpoints, or an operator browser page according to
  sensitivity.
- **expected_benefit:** Shortens diagnosis time for failed/stalled pipelines and
  makes quality regressions visible before users inspect bad results.
- **implementation_complexity:** `M`
- **risks_or_tradeoffs:** Database diagnostics can leak operational detail, so
  admin-only or CLI-only boundaries need to be explicit.
- **suggested_validation:** Add snapshot tests for health JSON and seeded
  fixture checks for failed jobs, stalled jobs, missing annotations, and missing
  embeddings.

#### BL-024: Evaluation and Drift Artifacts

**Backlog candidate:**

- **id:** `BL-024`
- **title:** Evaluation and drift artifacts
- **summary:** Store evaluation runs and corpus snapshots so NLP quality can be
  compared over time.
- **category:** `nlp`
- **type:** `research`
- **status:** `parked`
- **complexity:** `L`
- **priority:** `medium`
- **owner:** `shared`
- **rationale:** The NLP source treats evaluation and maintenance as part of
  composition, but Doc-Ray currently tracks structural health more than model or
  corpus quality drift.
- **description:** Define minimal 4NF relations or artifacts for
  `evaluation_run`, `evaluation_metric`, `gold_sample`, and `corpus_snapshot`.
  Start with lexical/entity distributions, extraction empty/error rates,
  annotation coverage, and review acceptance/rejection rates.
- **acceptance_criteria:**
  - "Fixture corpus snapshots can be compared deterministically."
  - "At least lexical/entity distribution drift is reported."
  - "Metrics cite the stored facts and pipeline/tool versions behind them."
- **dependencies:** `BL-018`, `BL-019`
- **references:** `docs/nlp_composition_mastery.md`,
  `src/doc_ray/application/corpus_health.py`, `docs/DATABASE_SPEC.md`
- **source:** Session finding: no first-class evaluation/drift model despite
  source-grounded NLP maintenance requirement.
- **tags:** `evaluation`, `drift`, `metrics`, `nlp`

**Proposal analysis:**

- **standards_alignment:** Builds measurable quality evidence before expanding model
  claims.
- **problem:** Doc-Ray has structural health checks but no first-class way to
  compare NLP quality or corpus drift across time, model versions, or datasets.
- **codebase_evidence:** `corpus_health.py` reports counts and missing-work
  conditions, while no inspected schema/API artifact represented evaluation
  runs, gold samples, metrics, or corpus snapshots.
- **supporting_graphql_knowledge:** The source corpus treats model evaluation
  and benchmarking as part of NLP composition; the MDPI source explicitly notes
  benchmarking gaps in hate-speech detection literature.
- **proposed_change:** Define minimal evaluation and drift artifacts before
  adding heavier model-workflow features.
- **expected_benefit:** Gives maintainers measurable evidence for model changes,
  corpus growth effects, and annotation-quality claims.
- **implementation_complexity:** `L`
- **risks_or_tradeoffs:** This can become research infrastructure creep unless
  the first version is limited to deterministic corpus snapshots and simple
  distributions.
- **suggested_validation:** Create fixture snapshots with expected lexical and
  entity distributions, then verify deterministic diff output.

#### BL-025: Lexeme/Token Projection Invariant

**Backlog candidate:**

- **id:** `BL-025`
- **title:** Lexeme/token projection invariant
- **summary:** Define whether `lexeme_occurrences` is a materialized projection
  over `tokens` or an independent primary fact, then enforce parity.
- **category:** `persistence`
- **type:** `tech-debt`
- **status:** `ready`
- **complexity:** `M`
- **priority:** `medium`
- **owner:** `shared`
- **rationale:** The spaCy writer stores token-level lexeme links and also writes
  `lexeme_occurrences`; without an invariant, lexical counts can drift.
- **description:** Either keep `lexeme_occurrences` as a materialized projection
  and add parity tests against `tokens.lexeme_uid`, or migrate it to a view once
  browser/API compatibility allows.
- **acceptance_criteria:**
  - "Token lexeme counts and `lexeme_occurrences` counts are parity-tested."
  - "GraphQL/browser docs state whether occurrences are projections."
  - "Any migration preserves browser lexeme/KWIC behavior."
- **references:** `src/doc_ray/adapters/annotation_spacy.py`, `webapp/app.py`,
  `docs/DATABASE_SPEC.md`
- **source:** Session finding: lexical projection is useful but under-specified.
- **tags:** `lexemes`, `tokens`, `projection`, `persistence`

**Proposal analysis:**

- **standards_alignment:** Preserves relational clarity, projection traceability, and
  regression coverage.
- **problem:** Token-level lexeme links and `lexeme_occurrences` can drift if
  the projection relationship is not defined and tested.
- **codebase_evidence:** The spaCy writer stores `tokens.lexeme_uid` and also
  writes `lexeme_occurrences`; browser/API lexeme views depend on the
  occurrence projection.
- **supporting_graphql_knowledge:** The source corpus treats tokenization,
  lemmatization, lexical representation, and counts as foundational NLP
  preprocessing and representation facts.
- **proposed_change:** Declare whether occurrences are materialized projections
  or primary facts, then enforce parity or migrate to a view when compatible.
- **expected_benefit:** Stabilizes lexical statistics, KWIC behavior, and future
  token/lexeme GraphQL fields.
- **implementation_complexity:** `M`
- **risks_or_tradeoffs:** Moving to a view can affect browser/API performance;
  keeping materialization requires reliable refresh/parity checks.
- **suggested_validation:** Add parity tests comparing token-derived lexeme
  counts with occurrence rows for fixture documents and reannotation flows.

## 6. Implementation Order

### Phase 0: Baseline and Drift Guard

Goal: make the current state explicit before changing behavior.

- Deliver `BL-016` so the capability matrix becomes the live map from domain
  claims to code evidence.
- Deliver the non-invasive half of `BL-020`: `ReadRepository` signature repair
  and parity tests for document, lexeme, KWIC, and search reads.
- Classify the health/analyzer modules from `BL-023` as accepted,
  experimental, or deferred before making them part of operator docs.

Exit criteria: the matrix distinguishes schema-only, writer-backed,
API-exposed, UI-exposed, health-checked, and tested capabilities; no existing
GraphQL field or shared read claim is undocumented.

### Phase 1: Provenance and Validation

Goal: make produced NLP artifacts auditable before exposing more of them.

- Deliver `BL-018` for spaCy annotation provenance.
- Deliver `BL-019` for annotation structural validation.
- Deliver `BL-022` for explicit language/model capability and fallback policy.
- Start `BL-025` with parity tests if validation exposes token/lexeme drift.

Exit criteria: a successful annotation run has a provenance event, validation
status, supported-language record, and regression test evidence.

### Phase 2: Layer-Aware GraphQL

Goal: expose the already-produced linguistic layers through bounded,
document-scoped GraphQL fields.

- Deliver `BL-017` for `AnnotationRun`, `Token`, `TokenFeature`, `Dependency`,
  `Chunk`, and `CorefChain` GraphQL types and read adapters.
- Keep `BL-009` constraints active for every new nested field.
- Update `BL-016` after each exposed layer lands.

Exit criteria: clients can inspect a document's token, morphology, syntax,
chunk, entity, coreference, provenance, and validation summaries without direct
SQL.

### Phase 3: Canonicalization and Operator Surfaces

Goal: make human-in-the-loop identity resolution and health operations visible.

- Deliver `BL-021` for canonical registry, form-index, entity-link,
  unresolved-mention, and review GraphQL queries.
- Add browser review/canonical pages only after the API contract is stable.
- Deliver the accepted portion of `BL-023` through admin-safe operator surfaces.

Exit criteria: unresolved mentions, canonical forms, review rows, and corpus
health can be inspected through supported surfaces; operational details remain
behind admin-safe boundaries.

### Phase 4: Evaluation, Drift, and Representation Workflows

Goal: move from structural completeness to measurable NLP quality and
maintenance.

- Deliver `BL-024` for evaluation runs, corpus snapshots, and drift reports.
- Finish `BL-025` by deciding whether `lexeme_occurrences` remains materialized
  or becomes a view.
- Add embedding production/read APIs only after provenance, validation, and drift
  evidence are in place.

Exit criteria: maintainers can compare two pipeline runs or corpus snapshots
with stored metrics, identify drift, and trace the representation/model choices
behind the result.

## 7. Expected Benefits

- The GraphQL API becomes a faithful front door to Doc-Ray's NLP graph, not only
  a document/lexeme browser API.
- Documentation stops mixing schema capacity with production capability.
- Annotation output becomes reproducible and structurally validated.
- Browser/API/export read drift becomes detectable or eliminated.
- Canonicalization review becomes an inspectable workflow, not only job JSON.
- Operators gain health signals before data quality issues reach users.
- Future NLP layers can be added against a clear contract: schema, writer,
  provenance, read adapter, GraphQL, UI, tests, and health.

## 8. Risks and Open Questions

- **API scope risk:** exposing every table directly would make GraphQL noisy.
  Prefer task-shaped, layer-aware fields over table dumps.
- **Performance risk:** syntax, token, and coreference queries can be large.
  Require bounded pagination and query complexity limits.
- **Migration risk:** browser SQL consolidation can become a sync/async
  abstraction fight. Start with parity tests if refactoring is not immediately
  clean.
- **Product risk:** evaluation tables can become abstract before real use cases
  stabilize. Start with metrics already derivable from stored corpus facts.
- **Security risk:** database health and operator diagnostics may reveal
  sensitive details. Gate admin surfaces.
- **Language policy question:** should unsupported language annotation fail,
  warn-and-fallback, or use a generic tokenizer?
- **Projection question:** should `lexeme_occurrences` remain a materialized
  compatibility layer, or become a view over `tokens` once the browser and API
  are ready?
- **Review workflow question:** which review actions are required in the first
  product slice: approve, reject, merge, create canonical, edit aliases, or only
  inspect?
