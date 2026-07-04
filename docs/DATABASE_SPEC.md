---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-04"
---

# doc-ray — Database Specification

**Status:** documents the foundational migrations 001–012 (§7) — the core
document/NLP-annotation model this spec's invariants and design principles
were derived from. The schema has since grown well past 012 (canonical-entity
registry, job queue, RBAC, backups, and more); **the complete, current
migration set and generated ERD/data dictionary live in
[`DATABASE.md`](DATABASE.md)**, re-derived from the live schema on every run
— treat that document as the source of truth for "what migrations exist
today." Engine: PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`). Every
layer is covered by TDD integration tests.
**Scope:** this document specifies the *foundational* design of the database
— its purpose, its schema, the invariants it upholds, and how it was designed
to grow (a design that has since held: every addition past 012 is a purely
additive migration under these same principles, not a revision of them).

---

## 1. Purpose

doc-ray is a **document store and NLP annotation platform**. It ingests source
documents (today: PDFs), extracts their text, and decomposes them into a
**fully normalized (4NF), provenance-tracked, reproducible relational model**.

It exists to answer three classes of question over a corpus, in one place:

1. **Bibliographic / retrieval** — *what documents do we have, by whom, about
   what, in what language, and where did the bytes come from?*
2. **Structural / linguistic** — *decompose each document into sentences, words,
   and lexemes; find every occurrence of a lexeme across the corpus; compute
   type/token statistics; run concordances.*
3. **Provenance / reproducibility** — *which tool and version produced each
   artifact, from which exact bytes, and can we reproduce it?*

The design goal is that the database is the **single source of truth**: nothing
of value lives only in files or in JSON blobs. Original bytes live in object
storage (content-addressed); everything else — metadata, structure, annotations,
and the full audit trail — is queryable relational data.

---

## 2. Design principles

| Principle | How it is realized |
|---|---|
| **4NF normalization** | No arrays, no multivalued JSON bags. Every independent multivalued fact is its own relation. `documents` carries only single-valued attributes. |
| **Stand-off annotation** | Character offsets are the universal address. A shared `spans` registry makes every `(document, start, end)` a first-class, FK-referenceable entity. |
| **Time-ordered identity** | Row-level artifacts (sentences, provenance events, annotation runs) are keyed by **ULID** (26-char Crockford base32); lexical/text sort = chronological order. |
| **Content addressing** | Documents are identified and deduplicated by `content_sha256`; a hash chain in provenance links bytes → text. |
| **Layer-aware annotation** | The same document can be annotated by many tools/layers simultaneously; each run is scoped by `(document, layer, tool)`. |
| **Derive, don't duplicate** | Anything recomputable from stored data is a **view**, not a stored column (surface text, lexeme forms, canonical keys). |
| **Auditable & reproducible** | An append-only provenance log records every processing step with tool, version, and input/output hashes. |

---

## 3. The layered data model

```
                         ┌─────────────┐
                         │  documents  │  (entity: identity + content-identity + lineage)
                         └──────┬──────┘
        ┌───────────────┬───────┼────────────┬───────────────┬──────────────┐
        │               │       │            │               │              │
  document_authors  document_  document_  document_      document_      document_
   → authors        tags →     identifiers  metadata       content        provenance
                     tags                                  (full_text)   → provenance_params
                                                               │
                                          ┌────────────────────┼───────────────────┐
                                          │                    │                    │
                                       spans  ◄──────────── sentences           annotations
                                    (offset registry)     (span_id)          (layer, tool)
                                          ▲                                        │
                                          └──────────── lexeme_occurrences ────────┘
                                                        (annotation × span → lexeme)
                                                               │
                                                            lexemes   (global type identity)
```

The tables group into six layers plus meta:

### 3.1 Document entity layer
The document is the root entity; its independent multivalued facts are separate
relations (this is the 4NF core).

- **`documents`** — one row per document. Single-valued attributes only:
  `id uuid PK`, `title`, `subtitle`, `doc_type`, `mime_type`, `language`,
  `publisher`, `published_on`, `license`, `original_filename`, `source_uri`
  (object-store URI), `content_sha256` (unique — dedupe/integrity),
  `byte_size`, `page_count`, `char_count`, `text_sha256`, `derived_from`
  (self-FK for lineage), `revision`, `status`, `error`, `created_at`,
  `updated_at`. **No arrays, no jsonb.**
- **`authors` / `document_authors`** — author dimension (deduped, global) + junction
  with `position` (author order). A shared author is one row referenced by many docs.
- **`tags` / `document_tags`** — tag dimension + junction.
- **`document_identifiers`** `(document_id, scheme, value)` — DOI / ISBN / URL, one
  row per identifier.
- **`document_metadata`** `(document_id, key, value)` — format-specific attributes
  (e.g. `pdf_producer`) as EAV rows, not a jsonb bag.

### 3.2 Content layer
- **`document_content`** `(document_id 1:1, full_text, fts)` — the canonical
  extracted text and a generated `tsvector` for full-text search. **All offsets
  everywhere in the database index into this `full_text`.**

### 3.3 Span registry (the annotation backbone)
- **`spans`** — `id uuid PK`, `document_id`, `char_start`, `char_end`,
  `UNIQUE(document_id, char_start, char_end)`. The single addressable notion of
  "a stretch of a document." Every structural and annotation layer references a
  span rather than repeating an offset triple. This is what lets future graph
  layers (dependency, coref, SRL, entity links) attach by FK.

### 3.4 Structural layer
- **`sentences`** — `id ulid PK`, `document_id`, `ordinal` (reading order),
  `text` (whitespace-normalized), `span_id → spans`. Offsets are obtained by
  joining `spans`. `UNIQUE(document_id, ordinal)`.

### 3.5 Lexical / NLP annotation layer
- **`annotations`** — one analyzer run: `id ulid PK`, `document_id`, `layer`
  (e.g. `lexeme`), `tool` (e.g. `rule/en-approx`, `spacy/en_core_web_sm@3.8`),
  `tool_version`, `created_at`, `UNIQUE(document_id, layer, tool)`. Multiple runs
  (rule + spaCy, or successive versions) coexist per document.
- **`lexemes`** — global, analyzer-independent type identity:
  `uid text PK` (e.g. `lx_en.v_3f9a21c0`), `lang`, `pos`, `lemma` (NFC+casefold),
  `UNIQUE(lang, pos, lemma)`. The same lexeme is shared across the whole corpus.
- **`lexeme_occurrences`** — the lexeme-layer occurrence table:
  `(annotation_id, span_id)` PK, `lexeme_uid → lexemes`. For spaCy runs this is
  a materialized projection of non-null `tokens.lexeme_uid` rows from the same
  annotation pass, retained as a table so existing GraphQL and browser KWIC
  reads keep their stable contract. Nothing derivable (surface, lemma, pos,
  lang) is stored here.
- **Views:** `lexeme_occurrence_detail` (occurrence + derived surface + lexeme
  attributes), `lexeme_forms` (distinct surfaces per lexeme), `lexeme_keys`
  (canonical `lang:pos:lemma`).

### 3.6 Provenance layer (append-only audit)
- **`document_provenance`** — one immutable event per processing step:
  `id ulid PK`, `document_id`, `kind` (`acquired|stored|extracted|segmented|
  annotated|…`), `tool`, `tool_version`, `occurred_at`, `actor`, `input_sha256`,
  `output_sha256`, `note`. ULID key ⇒ the log sorts chronologically.
- **`provenance_params`** `(event_id, key, value)` — per-event parameters as EAV.

### 3.7 Meta
- **`schema_migrations`** `(filename PK, applied_at)` — which migration files have run.
- **`backups`** `(id PK, created_at, status, object_key, schema_version, archive_bytes,
  table_rows, blob_count, blob_bytes, doc_count, note, error)` — catalogue of
  full-corpus snapshot archives (a binary-`COPY` database dump plus every
  object-store blob, bundled as one `.tar.gz`). Operational: this table and the
  `jobs` queue are excluded from the snapshot itself and from restore, so a
  restore never clobbers the queue or the archive index.
- **`ulid` domain** — `char(26)` constrained to `^[0-7][0-9A-HJKMNP-TV-Z]{25}$`
  (the Crockford alphabet), enforced at the type level.

### 3.8 Entity canonicalization (post-foundational addition; migrations 021–023, 025)

A shared registry (design: [`canonicalization_pipeline.md`](canonicalization_pipeline.md))
turning divergent mention surfaces into one canonical spine, layered on top of
the `entity_mention`/`entity_link` stand-off model from §3 without changing it.

- **`canonical`** `(id bigserial PK, wikidata_id UNIQUE NULL, preferred, entity_type,
  description, lang_primary, embedding vector(768) NULL, created_at, updated_at)`
  — one row per real-world entity or concept. `bigserial`, not `ulid`/`uuid`: a
  registry (grows by seeding/resolution events, not document ingestion), the one
  deliberate identity-scheme departure in the schema, documented here rather than
  silently inconsistent. `wikidata_id` is `UNIQUE` but nullable — a registry-local
  concept with no KB counterpart is legitimate. HNSW index on `embedding` (unused
  until an embedding backend is ported — see `SHOWCASE.md`).
- **`form_index`** — the remissive index: `(form_norm, form_surface, canonical_id
  → canonical, lang, form_type, confidence, occurrences, source_types text[],
  first_seen)`, PK `(form_norm, canonical_id, lang)`. `form_norm` is an
  NFC+casefold+transliterated normalization (`domain/canonical_norm.py`) so
  "Zelensky"/"Zelenskyy"/"Зеленський" can share one lookup key. A form is
  reinforced (occurrence count +=, confidence takes the max), never duplicated,
  on repeat sightings — the flywheel that makes resolution cheaper as the corpus
  grows.
- **`resolution_review`** `(id ulid PK, mention_id → entity_mention, candidates
  jsonb, proposed_canonical_id NULL, confidence, status CHECK (pending|approved|
  rejected|merged), reason, reviewer NULL, created_at, decided_at NULL)` — the
  human-in-the-loop queue for ambiguous or low-confidence frontier-resolver
  verdicts (PALS: nothing auto-commits below the confidence threshold).
- **`canonical_merge`** `(from_id, into_id, actor, reason, merged_at)` — audit
  trail for reconciling two canonicals discovered to be the same entity.
- **`seed_suggestion`** `(id ulid PK, document_id → documents, term, occurrences,
  source CHECK (mention|lexeme), qid NULL, label NULL, description NULL,
  entity_type NULL, status CHECK (pending|approved|rejected|seeded), created_at,
  decided_at NULL)`, `UNIQUE (document_id, term)` — the curatable staging area for
  domain seeding (migration 025): a worker job mines and KB-resolves candidate
  terms into this table; nothing reaches `canonical` until a human approves.
  `seeded` is a final state (a decided row is never silently re-mined).

---

## 4. Identity & addressing schemes

| Concept | Key | Rationale |
|---|---|---|
| Document | `uuid` surrogate + `content_sha256` natural key | dedupe by content; stable surrogate for FKs |
| Sentence, provenance event, annotation run | **ULID** | time-sortable identity; `ORDER BY id` = chronological |
| Span | `uuid` surrogate + `(document_id, char_start, char_end)` natural key | offsets are the real identity; surrogate for compact FKs |
| Lexeme | `uid` string (`lx_<lang>.<postag>_<hash>`) + `(lang,pos,lemma)` natural key | human-readable, collision-safe alias; global identity |
| Any annotation target | a **span** | one stand-off address for every layer |

---

## 5. 4NF guarantees (what "normalized" means here)

- **1NF** — every column is atomic; there are no array or jsonb columns holding
  multiple values on `documents`, `document_provenance`, `lexemes`, etc.
- **BCNF** — for the identity tables (`documents`, `lexemes`, `spans`,
  `annotations`), every determinant is a candidate key.
- **4NF** — independent multivalued facts are separated: a document's authors,
  tags, identifiers, and metadata are four distinct relations, so there is no
  multivalued dependency of one on another. A lexeme's surface *forms* and a
  provenance event's *params* are child relations (or views), never arrays.
- **No stored derivable data on hot paths** — surface text, lemma/pos/lang of an
  occurrence, and lexeme forms are views; storing them on the occurrence would be
  a transitive dependency (3NF violation) or redundant multivalued data.

---

## 6. Invariants (verified properties)

1. **Offset fidelity** — for every sentence and every occurrence,
   `full_text[span.char_start : span.char_end]` recovers the unit's text
   (token-equal). Verified across the corpus (0 mismatches).
2. **ULID monotonicity** — within a document, ULID order equals reading order for
   sentences; provenance/annotation ULIDs sort by creation time. (0 ordering
   violations.)
3. **Provenance hash chain** — `stored.output_sha256 = content_sha256`,
   `extracted.input_sha256 = content_sha256`,
   `extracted.output_sha256 = text_sha256`. Proves stored text derives from the
   recorded bytes via the recorded tool version. (0 broken chains.)
4. **Roundtrip** — re-extracting a source PDF with the *provenance-recorded* tool
   version reproduces the stored text and all sentences exactly. (Roundtrip harness
   passes; a different extractor version is correctly detected as a mismatch.)
5. **Deduplication** — identical bytes (`content_sha256`) are never re-ingested;
   identical `(document, start, end)` is never a second span; identical
   `(lang, pos, lemma)` is never a second lexeme.

---

## 7. Complete migration set (001 → 012)

Every migration builds on the same three primitives: **stand-off spans**,
**layer-aware annotation runs**, and **one typed relation per layer** (never a
generic `edge(…, attributes JSON)` table). Multivalued facts always decompose
into child relations; derivable encodings (BIO tags, CoNLL-U rows, surface
strings) are views. NLP layer numbers refer to the 20-layer stack in
`nlp_annotation_layers.md`.

### 7.1 Applied (001–007)

| # | File | Adds | NLP layers |
|---|---|---|---|
| 001 | `init` | `documents`, `document_content` (+ FTS) | 1 (partial) |
| 002 | `sentences` | `sentences`, `ulid` domain | 2 |
| 003 | `document_entity` | bibliographic identity, content-identity, lineage columns | — |
| 004 | `normalize_4nf` | `authors`/`document_authors`, `tags`/`document_tags`, `document_identifiers`, `document_metadata`, `provenance_params` | — |
| 005 | `lexemes` | `lexemes`, `annotations`, `lexeme_occurrences` + 3 views | 5, 6, +lang-id, +type-id |
| 006 | `schema_migrations` | migration ledger table | — |
| 007 | `spans` | `spans` registry; layer-aware `annotations`; `sentences`/`lexeme_occurrences` refactored onto spans | backbone; 2, 3 |

### 7.2 Applied (008–012) — the remaining NLP stack

All new fact tables carry `annotation_id ulid REFERENCES annotations(id)` (the
run/tool/version) and address text through `spans`. ULID PKs throughout. Each
migration below is live and exercised by an integration test suite
(`src/doc_ray/tests/test_00N_*.py`) that inserts the worked example
("Elena founded a startup in Berlin.") into its tables and queries it back.

#### 008 · `tokens_morphology` — tokens, morphology, subwords (layers 3, 4, 6, 7)
Formalizes the token as an addressable entity that syntax/morphology reference,
and decomposes UD FEATS (`Number=Sing|Tense=Past`) into rows.

```sql
CREATE TABLE tokens (
    id            ulid PRIMARY KEY,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id       uuid NOT NULL REFERENCES spans(id),
    sentence_id   ulid REFERENCES sentences(id),
    ordinal       int  NOT NULL,                    -- token index within sentence (CoNLL-U ID)
    lexeme_uid    text REFERENCES lexemes(uid),     -- lemma + UPOS via the lexeme
    upos          text,                             -- Universal POS
    xpos          text,                             -- language-specific tag
    UNIQUE (annotation_id, span_id)
);
CREATE TABLE token_feature (                        -- UD FEATS, one row per key
    token_id ulid NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    feature  text NOT NULL, value text NOT NULL,
    PRIMARY KEY (token_id, feature)                 -- (Tense→Past),(Mood→Ind)
);
CREATE TABLE subword (                              -- BPE/WordPiece pieces, ordered
    token_id ulid NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    position int NOT NULL, piece text NOT NULL,
    PRIMARY KEY (token_id, position)
);
```
*Note:* `tokens` generalizes `lexeme_occurrences`; the latter remains the
materialized lexeme-layer projection over token lexeme links. Browser and
GraphQL lexeme/KWIC reads use this projection table today; a future
consolidation may make it a view over `tokens` only if those read contracts stay
equivalent.

#### 009 · `syntax` — chunking, constituency, dependencies (layers 8, 9, 10)
```sql
CREATE TABLE chunk (                                -- BIO collapsed to spans: NP/VP/PP
    id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id uuid NOT NULL REFERENCES spans(id), chunk_type text NOT NULL);
CREATE TABLE constituent (                          -- phrase-structure tree (adjacency list)
    id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    parent_id ulid REFERENCES constituent(id) ON DELETE CASCADE,
    label text NOT NULL, span_id uuid REFERENCES spans(id));
CREATE TABLE dependency (                           -- each token has exactly one head
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    dependent_token_id ulid NOT NULL REFERENCES tokens(id),
    head_token_id ulid REFERENCES tokens(id),       -- NULL = root
    deprel text NOT NULL,
    PRIMARY KEY (annotation_id, dependent_token_id));
```

#### 010 · `reference` — NER, entity linking, coreference (layers 11, 12, 13)
```sql
CREATE TABLE entity_mention (
    id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id uuid NOT NULL REFERENCES spans(id), entity_type text NOT NULL);   -- PER/ORG/GPE/DATE
CREATE TABLE entity_link (                          -- mention -> KB id; NIL = no row
    mention_id ulid NOT NULL REFERENCES entity_mention(id) ON DELETE CASCADE,
    scheme text NOT NULL, kb_id text NOT NULL, confidence real,
    PRIMARY KEY (mention_id, scheme));              -- wikidata:Q64
CREATE TABLE coref_chain (
    id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE);
CREATE TABLE coref_mention (                        -- chain 1:N mention
    chain_id ulid NOT NULL REFERENCES coref_chain(id) ON DELETE CASCADE,
    span_id uuid NOT NULL REFERENCES spans(id),
    PRIMARY KEY (chain_id, span_id));
```

#### 011 · `meaning_discourse` — SRL, WSD, AMR, discourse, temporal, sentiment (layers 14–19)
```sql
-- 14 · semantic role labeling: predicate 1:N argument
CREATE TABLE srl_predicate (id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    token_id ulid NOT NULL REFERENCES tokens(id), frame text NOT NULL);        -- found.01
CREATE TABLE srl_argument (predicate_id ulid NOT NULL REFERENCES srl_predicate(id) ON DELETE CASCADE,
    span_id uuid NOT NULL REFERENCES spans(id), role text NOT NULL,
    PRIMARY KEY (predicate_id, span_id));                                       -- ARG0/ARG1/ARGM-LOC
-- 15 · word sense disambiguation
CREATE TABLE word_sense (token_id ulid NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE, sense_key text NOT NULL,
    PRIMARY KEY (token_id, annotation_id));                                     -- vision.n.03
-- 16 · AMR: rooted reentrant graph (DAG) -> node + edge, not parent_id
CREATE TABLE amr_node (id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    sentence_id ulid REFERENCES sentences(id), concept text NOT NULL);
CREATE TABLE amr_edge (id ulid PRIMARY KEY, parent_node_id ulid NOT NULL REFERENCES amr_node(id) ON DELETE CASCADE,
    child_node_id ulid NOT NULL REFERENCES amr_node(id), role text NOT NULL);
-- 17 · discourse (PDTB): relation between two argument spans
CREATE TABLE discourse_relation (id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    connective_span_id uuid REFERENCES spans(id), arg1_span_id uuid NOT NULL REFERENCES spans(id),
    arg2_span_id uuid NOT NULL REFERENCES spans(id), sense text NOT NULL);
-- 18 · TimeML: TIMEX3 + EVENT + TLINK
CREATE TABLE timex (id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id uuid NOT NULL REFERENCES spans(id), timex_type text NOT NULL, value text);   -- DATE, 2023-03
CREATE TABLE event (id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    token_id ulid NOT NULL REFERENCES tokens(id), event_class text);
CREATE TABLE tlink (id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    source_id ulid NOT NULL, target_id ulid NOT NULL, relation text NOT NULL);           -- BEFORE/AFTER/INCLUDES
-- 19 · sentiment & pragmatics
CREATE TABLE sentiment (id ulid PRIMARY KEY, annotation_id ulid NOT NULL REFERENCES annotations(id) ON DELETE CASCADE,
    span_id uuid NOT NULL REFERENCES spans(id), polarity text NOT NULL, score real, target_span_id uuid REFERENCES spans(id));
```

#### 012 · `embeddings` — distributed representations (layer 20)
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE embedding (                            -- one vector per (span, model)
    span_id uuid NOT NULL REFERENCES spans(id) ON DELETE CASCADE,
    model   text NOT NULL,                          -- e.g. 'bge-base-en@1.5'
    vector  vector(768) NOT NULL,
    PRIMARY KEY (span_id, model));
CREATE INDEX idx_embedding_hnsw ON embedding USING hnsw (vector vector_cosine_ops);
```

### 7.3 Coverage after 012

| NLP layer | Home |
|---|---|
| 1 physical structure | `documents`, `sentences` (+ `constituent` for syntax trees) |
| 2 sentences · 3 tokens | `sentences`, `tokens` (over `spans`) |
| 4 subwords | `subword` |
| 5 lemma · 6 POS | `lexemes`, `tokens.upos/xpos` |
| 7 morphology | `token_feature` |
| 8 chunking · 9 constituency · 10 dependency | `chunk`, `constituent`, `dependency` |
| 11 NER · 12 entity linking · 13 coreference | `entity_mention`, `entity_link`, `coref_*` |
| 14 SRL · 15 WSD · 16 AMR | `srl_*`, `word_sense`, `amr_*` |
| 17 discourse · 18 temporal · 19 sentiment | `discourse_relation`, `timex`/`event`/`tlink`, `sentiment` |
| 20 embeddings | `embedding` (pgvector) |
| language identification | `lexemes.lang` / annotation-run scope |

At migration 012 the database holds the **entire** symbolic NLP stack plus the
non-symbolic (vector) substrate, every layer in 4NF, every unit addressed by a
shared span, every run attributed to a tool and version.

---

## 8. Ingestion & readback surfaces

- **Ingest** (`doc_ray.entrypoints.cli`) — file → object storage + `documents` +
  content + sentences + provenance, atomically per document.
- **Annotate** (`doc_ray.entrypoints.annotate`) — stored `full_text` → spans +
  lexemes + occurrences + an `annotated` provenance event, per `(layer, tool)` run.
- **Export** (`doc_ray.entrypoints.export`) — reassembles the full normalized record of
  any document (entity + all satellites + provenance + content + sentences) as JSON.
- **Roundtrip** (`scripts/roundtrip.py`) — verifies stored data reproduces the source.
- **GraphQL API** (`doc_ray.entrypoints.api`) — async read-only surface over the same
  store; write mutations enqueue background jobs run by `doc_ray.entrypoints.worker`.
  Lexeme and KWIC reads consume `lexeme_occurrences` as the materialized
  projection of token-level lexeme links, not as an independent lexical fact.

All are `DATABASE_URL`-driven and run identically on host or in the container.
