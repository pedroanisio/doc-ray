---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code; retrieval-wave contract/profile corrections by OpenAI GPT-5 via Codex"
  date: "2026-07-27"
---

# How doc-ray Uses NLP

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## Scope

This document describes the natural-language processing that **actually runs** in
this codebase: which components produce linguistic structure, what algorithm each
one uses, where the output is stored, how it is verified, and how it is read back.
Every claim below points at a file you can open.

It is the third of four NLP documents, and they answer different questions:

| Document | Answers |
|---|---|
| [`nlp_annotation_layers.md`](nlp_annotation_layers.md) | *What is the complete 20-layer target?* — the notation reference, with a worked synthetic example |
| [`nlp_capability_matrix.md`](nlp_capability_matrix.md) | *Which layers are production-supported today?* — a per-layer status table with a static drift guard |
| **This document** | *How does the running system do NLP?* — algorithms, data flow, verification, and exposure |
| [`nlp_composition_mastery.md`](nlp_composition_mastery.md) | *How should one think about NLP systems generally?* — craft essay, not codebase-specific |

Related: [`architecture.md`](architecture.md) (hexagonal layering),
[`canonicalization_pipeline.md`](canonicalization_pipeline.md) (entity-resolution
design), [`DATABASE.md`](DATABASE.md) (table dictionary).

---

## 1. Design posture

Four commitments shape every NLP decision in this codebase. They explain choices
that would otherwise look conservative.

**Stand-off, never inline.** No annotation ever mutates the text. Every layer
references a `spans` row — a `(document_id, char_start, char_end)` interval into
the immutable `document_content.full_text`. Tokens, entity mentions, chunks,
coreference members, embeddings, and time expressions are all span references.
This is what makes a citation offset stable across re-annotation and what lets
several annotation layers claim overlapping text without conflict.

**Deterministic before statistical, statistical before generative.** The pipeline
prefers the cheapest mechanism that can produce a correct answer. Sentence
segmentation is rule-based, not model-based. Entity linking tries a deterministic
index lookup before it will spend a frontier-model call. Terminology detection is
a registry scan, not a classifier. A model is used where nothing deterministic
can substitute — part-of-speech, dependency parsing, NER — and a generative model
only where the deterministic and statistical paths have both already failed.

**Machine output is untrusted by default.** This is the codebase's PALS's Law
(see [`../CLAUDE.md`](../CLAUDE.md)): omissions, hallucinations, and silent
failures are statistical properties of the model class, so every ML/LLM output
crosses a verification boundary before it becomes a corpus fact. Section 8
enumerates each gate.

**Honest labelling over impressive labelling.** Several layers are populated by
frank heuristics — a 14-word sentiment lexicon, a four-connective discourse
matcher, a hash-of-bag-of-words "embedding". They are written under a tool tag
that says so (`doc-ray/semantic-heuristic`), and the capability matrix marks them
`writer-backed`, not `api-exposed`. The system never dresses a heuristic as a
model.

---

## 2. Where NLP sits in the system

```
   upload / CLI ingest
          │
          ▼
   ┌──────────────┐   extraction (PyMuPDF, pypdf, EPUB/DOCX/PPTX, markup)
   │  Stage 0     │   headings normalized to ATX markers inside the text
   │  extraction  │   → documents, document_content.full_text
   └──────┬───────┘
          │  synchronous, in the ingest transaction
          ▼
   ┌──────────────┐   domain.sentences.build_sentences
   │  sentence    │   rule-based, abbreviation-aware, offset-preserving
   │ segmentation │   → sentences (ULID, ordinal, span)
   └──────┬───────┘
          │
          │  ─── the ingest transaction commits here ───
          │
          ▼  job chain: annotate → semantic_enrich → canonicalize → visual_extract
   ┌──────────────┐   domain.lexemes.detect_language → spaCy model routing
   │  Stage 1     │   spaCy pipeline: tokens · UPOS/XPOS · FEATS · noun chunks
   │  annotation  │   · dependencies · NER · string-match coref · lexeme links
   └──────┬───────┘   → validation gate → one transaction → provenance
          │
          ├──────────► NER correction log re-applied (domain.ner_corrections)
          │
          ▼
   ┌──────────────┐   application.semantic_enrichment
   │  Stage 1.9   │   discourse connectives · TIMEX · events · sentiment
   │  enrichment  │   · hash-BoW sentence vectors
   └──────┬───────┘
          │
          ▼
   ┌──────────────┐   term_scan (registry surfaces) → candidate_generation
   │  Stage 2..6  │   (blocking) → resolution (frontier LLM, opt-in)
   │ canonicali-  │   → review queue → write-back to the remissive index
   │ zation       │
   └──────┬───────┘
          │
          ▼
   read surfaces: GraphQL · MCP · Flask webapp
```

The layering follows the hexagon. Pure linguistic logic lives in `doc_ray.domain`
(stdlib only): `sentences`, `lexemes`, `canonical_norm`, `entity_profiles`,
`ner_corrections`, `document_outline`. Model-bearing code lives in
`doc_ray.adapters` (`annotation_spacy`, `resolver_claude`, `resolver_ollama`,
`vision_encoder_openclip`, `vlm_ollama`). Orchestration lives in
`doc_ray.application`. The `Annotator` port (`ports/annotator.py`) is the seam:
it declares only `analyze(surfaces) -> [(lemma, upos)]`, satisfied both by the
zero-dependency `RuleAnalyzer` and by spaCy.

**Pipeline chaining.** `entrypoints/api/mutation.py` defines
`_PIPELINE_CHAIN = [annotate, semantic_enrich, canonicalize, visual_extract]`.
Everything in that chain is either deterministic or a local statistical model. The
frontier resolver and the GPU visual tiers are deliberately *not* chained — model
cost is always an explicit request, never a side effect of ingest.

---

## 3. Sentence segmentation

**Where:** `src/doc_ray/domain/sentences.py`, called from
`application/pipeline.py` during ingest.

Segmentation is a hand-written, dependency-free scanner rather than a model, and
it runs *before* any annotation — the sentence table is the citation substrate
every read surface addresses, so it must exist even for documents that never get
annotated.

The algorithm walks the text once. At each terminator (`.!?…`) it consumes any
run of following terminators and closing punctuation (so `?!"` closes together),
then requires whitespace-or-EOF, then requires the next non-space character to be
uppercase, a digit, or an opening bracket/quote. A candidate boundary is rejected
by `_is_false_boundary` when the token before the dot is a known abbreviation
(English titles, month abbreviations, Portuguese honorifics), a single initial
(`A.`), or an acronym pattern (`U.S`, `e.g`, `a.m`).

The module states an explicit **offset contract**:

```
full_text[char_start:char_end].split() == sentence.text.split()
```

Offsets point at the exact raw span; `text` is that span with internal whitespace
collapsed. This is what allows the reader to highlight a hit in the original text
while search matches against the normalized form.

`build_sentences` assigns each sentence a ULID from a per-document monotonic
factory, so ULID order and `ordinal` order agree. That ordinal is the address in
`read_document_sentences(from_ordinal, to_ordinal)` and in every MCP citation.

Note the deliberate boundary: spaCy also computes sentence boundaries, and they
are **not** used. The annotation pass instead maps each spaCy token's character
offset back onto the pre-existing sentence rows with a binary search
(`bisect_right` over sentence starts, `annotation_spacy.py`). Sentence identity
is owned by ingest and never invalidated by re-annotation.

---

## 4. Language identification

**Where:** `domain/lexemes.py::detect_language`.

Language detection is stopword-frequency voting over `en`, `fr`, `pt`. Each
candidate language has a hand-listed stopword set; the text is lowercased,
tokenized on word characters, and each language scores the number of tokens in
its set. The winner must clear `min_hits=3` and lead the runner-up by
`margin=2`, otherwise the configured default (`en`) is returned. Detection is
therefore *fail-soft*: an inconclusive signal never produces a confident wrong
answer.

One guard exists for a real failure: single-character tokens are dropped before
scoring. In technical prose, variable names (`e`, `a`, `o`, `u`) collide with
Portuguese and French stopwords and previously outvoted every genuine English
stopword, routing an English mathematics handbook to the Portuguese model.

Detection has two downstream consequences:

1. **Model routing** — it selects the spaCy pipeline (section 5).
2. **Search stemming** — `handle_annotate` writes the detected language to
   `documents.language`, and the migration-016 trigger rebuilds
   `document_content.fts` with the language-aware text-search config (section 7).

---

## 5. Stage 1 — the spaCy multi-layer annotation

**Where:** `src/doc_ray/adapters/annotation_spacy.py` (the writer),
`annotation_capabilities.py` (the model registry),
`annotation_validation.py` (the pre-persistence gate). Invoked by
`application/job_handlers.py::handle_annotate` or the
`doc_ray.entrypoints.annotate_spacy` CLI.

This single pass produces seven of the corpus's annotation layers from one spaCy
`Doc`, which is why they are written in one transaction and share one provenance
event: they are one observation of the document, not seven independent ones.

### 5.1 Model registry and language policy

`CAPABILITY_MODELS` is the single source of truth:

| Language | Model | Kind |
|---|---|---|
| `en` | `en_core_web_trf` | RoBERTa-based transformer pipeline |
| `pt` | `pt_core_news_sm` | CNN "news" pipeline |
| `fr` | `fr_core_news_sm` | CNN "news" pipeline |

English uses the transformer pipeline for higher-accuracy POS/NER/parsing;
`fr`/`pt` use the lighter CNN models because no transformer wheels are shipped
for them here.

`resolve_language` maps a detected language onto a model under a policy read from
`DOCRAY_SPACY_FALLBACK_POLICY` (default `fail`). Under `fail`, an unsupported
language raises `UnsupportedAnnotationLanguage` rather than silently annotating
Spanish with an English model; under `fallback`, it falls back to the default
language and records `language_fallback_used=true` plus the from/to pair in
provenance. `spacy_capabilities()` reports the registry at runtime — language,
model, `installed` (via `importlib.util.find_spec`), the nine supported layers,
and the active policy — so an operator can see whether a model is actually
present in the image rather than merely configured.

### 5.2 GPU and memory handling

Three details in this adapter exist because of measured production failures, and
they matter to anyone changing it:

- **Process-wide model cache.** `_NLP_CACHE` is module-level, not per-instance.
  The worker constructs a fresh `SpacyAnnotator` per job; a per-instance cache
  reloaded the transformer every job, each load pinning roughly 2 GB of VRAM that
  torch's caching allocator never returns, until every allocation OOM'd.
- **Mixed precision.** For `*_trf` models the config override
  `components.transformer.model.mixed_precision = True` runs the RoBERTa forward
  pass in fp16, halving the dominant activation tensors so long documents fit an
  8 GB GPU. This is inference precision only; POS/NER/dependency output is
  unchanged.
- **Inter-document release.** `_release_gpu_memory()` runs before each document:
  `gc.collect()`, `torch.cuda.empty_cache()`, and `cupy` pool release, each
  suppressed on failure. It holds peak VRAM at *model + one document* instead of
  *model + every document processed so far*. It is a no-op on CPU-only hosts —
  `spacy.prefer_gpu()` returns `False` there and everything runs on CPU.

`nlp.max_length` is raised to 5,000,000 characters so book-length documents are a
single `Doc`.

### 5.3 What the pass produces

| Output | Layer | Table | How it is derived |
|---|---|---|---|
| Tokens | 3 | `tokens` | spaCy tokenization; whitespace-only tokens skipped; `ordinal` is the document-global spaCy token index (`t.i`), so it continues across sentence boundaries and can contain gaps |
| Lemma + POS | 5, 6 | `tokens.upos` / `.xpos`, `lexemes` | `t.pos_` / `t.tag_`; alphabetic tokens get a lexeme link |
| Morphology | 7 | `token_feature` | `t.morph.to_dict()` → one `(feature, value)` row each |
| Noun chunks | 8 | `chunk` | `doc.noun_chunks`, written with `chunk_type='NP'` |
| Dependencies | 10 | `dependency` | `t.head` / `t.dep_`; a self-heading token becomes `head=NULL, deprel='root'` |
| Named entities | 11 | `entity_mention` | `doc.ents` → `(span, entity_type, surface)` |
| Coreference | 13 | `coref_chain`, `coref_mention` | **string match**, see below |
| Lexeme occurrences | 5 | `lexeme_occurrences` | projection of the token rows that carry a lexeme link |

**Coreference is a string match, not a model.** `_build_entities` groups mentions
whose label is `PERSON`/`PER`/`ORG`/`GPE`/`LOC` by casefolded surface; any surface
occurring at least twice becomes a chain. It resolves *repetition of the same
proper noun*, and nothing else — no pronouns, no bridging, no nominal
descriptions. `She`, `the company`, and `its` are invisible to it. This is a
genuine chain type (identity-by-repeated-name), and it is the whole of what
layer 13 currently contains.

**Spans are registered in one bulk call.** Token, entity, and chunk offsets are
collected first and passed to `db.ensure_spans` together, which returns the
`(start, end) -> span_id` map used by every subsequent row build. One round trip,
and overlapping layers share the same span row.

### 5.4 The lexeme layer — type identity

`domain/lexemes.py` implements a **type-level index** that sits outside the
classic 20 layers: a stable identifier per `(language, POS, lemma)` triple, which
per-occurrence annotations reference.

- `norm_lemma` = NFC normalization + strip + casefold.
- `canonical_key(lemma, pos, lang)` = `"en:VERB:run"`.
- `uid_candidate` = `lx_<lang>.<postag>_<blake2b-128 hex[:8]>`, e.g.
  `lx_en.v_1a2b3c4d`. The single-letter POS tag (`v`, `n`, `p`, `a`, `r`, …)
  keeps the id human-readable; collision-widening lives in
  `db.get_or_create_lexeme`, which owns the uniqueness check.

The lexeme layer is written as a *separate* annotation run (`layer='lexeme'`)
inside the same transaction as the spaCy run, because it is the projection the
webapp reader and the KWIC pages consume, and it needs its own replacement
semantics.

Two other writers target the same layer:

- `adapters/annotation.py` (`python -m doc_ray.entrypoints.annotate`) uses the
  zero-dependency `RuleAnalyzer`: a regex word tokenizer plus light English
  de-inflection (`-ies → -y`, `-(s|x|z|ch|sh)es`, trailing `-s`), with POS always
  `"X"`. It exists so the pipeline runs with no NLP dependency installed at all.
- `application/reannotate.py` (`python -m doc_ray.entrypoints.reannotate`) feeds
  the *same* `iter_words` tokenization into spaCy pre-tokenized, with `ner` and
  `parser` excluded, in 256-word batches. It refreshes lemma+POS without paying
  for the full pipeline, and keeps the project's own tokenization authoritative.

### 5.5 The validation gate

Before anything is written, `validate_spacy_annotation_output` runs a pure
structural check and raises `AnnotationValidationError` if any invariant fails:

- every span lies within `[0, len(text))` with `start < end`, and no span id repeats;
- token ids are unique, their spans are registered, their sentence references
  exist in *this document's* sentence set, and ordinals are non-negative,
  non-duplicated, and strictly increasing;
- every feature row and every dependency endpoint points at a token from this run;
- exactly one dependency row per token, with `head IS NULL` iff `deprel = 'root'`;
- entity, chunk, and coreference rows reference registered spans;
- `lexeme_occurrences` is *exactly* the projection of the token rows' lexeme links.

Because the function is pure — it takes prepared row tuples and returns a summary
— it is unit-testable without a database, and a structurally invalid annotation
graph never becomes visible: the failure happens before the transaction that
would publish it.

On success it returns `{validation_status: "passed", validation_errors: 0}`,
which is merged into both the job result and the provenance parameters.

### 5.6 Transaction and provenance

`_write` performs, in one transaction:

1. `DELETE FROM annotations WHERE document_id=… AND layer='spacy' AND tool=…` —
   replacement semantics per `(document, layer, tool)`, cascading the old rows.
2. Insert the new `annotations` row, then bulk-load tokens, features,
   dependencies, entities, and chunks with `COPY` (not `executemany`; psycopg3
   still issues one server-side statement per row that way).
3. Insert coreference chains and members.
4. Replace the `layer='lexeme'` run and `COPY` its occurrences.
5. Insert one `document_provenance` event of kind `annotated`, plus
   `provenance_params` rows recording annotation ids, detected vs. annotation
   language, model, fallback policy and decision, per-layer counts, replacement
   semantics, and the validation summary.

`handle_annotate` then re-applies the NER correction log (section 6), writes
`documents.language`, and refreshes the `document_stats` read-model rollup.

---

## 6. Human verification of NER

**Where:** `domain/ner_corrections.py` (pure logic),
`adapters/ner_corrections_store.py` (persistence), migration
`115_ner_corrections.sql` + `140_entity_surface_corrections.sql`, GraphQL
`correctEntityMention` / `entitySurfaceOccurrences` / `correctEntitySurface`,
MCP `correct_entity_mention` / `correct_entity_surface` /
`entity_mention_corrections`.

NER is the layer where model error is most visible to a reader, so it carries an
explicit human-override layer.

The design constraint is structural: `entity_mention` rows are
`ON DELETE CASCADE` from `annotations`, and every annotation run deletes and
rebuilds them. A correction stored on the mention row would be destroyed by the
next pass. A one-off correction is therefore keyed by the **stable span
identity** `(document_id, char_start, char_end)` in
`entity_mention_correction`. A systematic rule instead has honest `NULL` span
coordinates and an exact, case-sensitive `(surface, surface_original_type)`
matcher. Both shapes replay chronologically onto each fresh set of ML mentions.

Six actions are supported:

| Action | Meaning |
|---|---|
| `reclassify` | the span is an entity, but the type is wrong |
| `reject` | the span is not an entity at all |
| `add` | a real entity the model missed |
| `adjust_span` | the entity is real, the boundary is wrong |
| `reclassify_surface` | retype every exact surface/current-type match in the target Document |
| `reject_surface` | remove every exact surface/current-type match in the target Document |

`reconcile(mentions, corrections)` is pure, deterministic, and idempotent: it
indexes mentions by span, applies span and surface corrections in one order
(later decisions win), and returns the merged set sorted by span. Span actions
are upsert-style, so a fix survives even if the model stops emitting the span
entirely; surface actions apply to future exact matches created by later model
runs. Corrected mentions carry `source="human"` instead of `"ml"`, and the stored
correction records the original ML value and the reviewer — attributable and
reversible.

Because the reconcile needs no spaCy, a freshly recorded correction can be
applied through the cheap `apply_corrections` job rather than a full
re-annotation.

---

## 7. Search and retrieval paths

Three distinct retrieval mechanisms exist, with different semantics. Confusing
them is the most common source of "why didn't this match?".

| Path | Implementation | Semantics |
|---|---|---|
| `fullTextSearch` (document-level) | `persistence_fulltext_read.py::fulltext_search` | Postgres `tsvector`, ranked by `ts_rank`; `TERMS` uses `plainto_tsquery`, `PHRASE` uses `phraseto_tsquery`, and both are **stemmed per document language** |
| `searchSentences` (sentence-level) | `persistence_sentence_search.py::search_sentences` | `SUBSTRING` (default) is unchanged literal, case-insensitive containment; `PHRASE` verifies whole-word adjacency under `surface_word_v1`; **neither stems or ranks** |
| `lexemeKwic` | `persistence_read.py::lexeme_kwic` | exact lexeme-id lookup over `lexeme_occurrences`, with a ±45-character window |
| `termFrequency` (pre-flight) | `persistence_read_terms.py` + `domain/term_frequency.py` | exact lexeme-rollup measurement for one lexeme; whitespace phrases retain a rarest-component saturation ceiling; multi-word/unindexable terms fetch a cap-bounded candidate superset and report `surface_word_v1` verified sentence/occurrence counts |

The sentence scan reads its denominator from `corpus_stats.n_sentences`, which is
maintained from `document_stats` at the batch boundary rather than counted from the
`sentences` fact table on the request path (D-025). A positive scan paired with a
zero rollup is evidence that the denominator is unavailable, not evidence of a
zero-sentence corpus: GraphQL and MCP return `totalSentences: null` plus an explicit
note while preserving the absolute, bounded verified-sentence `floodRisk` band. Migration 146
repairs the full-refresh path and backfills existing tenants; `prod-check` verifies
the rollup against its maintained document-level inputs without scanning fact tables.

**Language-aware full-text search.** Migration `016_fts_language_aware.sql`
replaced the original generated `fts` column (hardcoded to the `english` config)
with a trigger-maintained `tsvector`. `docray_ts_config(lang)` maps
`en → english`, `pt → portuguese`, and **everything else → `simple`**. Two
triggers keep it current: one on content write, one on `documents.language`
change — necessary because language is detected *after* the content row exists.
Queries parse with the same per-document config, which
`tests/test_fts_language_config.py` guards statically. The default `TERMS` mode
preserves the historical all-terms match. `PHRASE` adds ordered phrase matching
without exposing raw `tsquery` syntax; stop words and stemming still follow each
Document's selected Postgres text-search configuration.

One consequence follows directly from the mapping: French is a supported
*annotation* language (`fr_core_news_sm`) but not a supported *search-stemming*
language — French documents fall to `simple`, so their full-text search is
unstemmed and stopword-free-by-omission. That is a real, current asymmetry
between the two registries, not a documentation gap.

**Sentence substring and phrase modes.** The default remains the historical
`match="substring"`: one parameterized `match_clause`, the same keyset order and
KWIC, and no stemming or word boundaries. This is intentionally different from
full-text phrase mode. A short query can match inside a longer word, and changing
that default would invalidate existing retrieval and coverage behavior.

Opt-in `match="phrase"` is stage 1 of the phrase architecture. Adapter SQL builds
a correct candidate superset by AND-ing each `surface_word_v1` query word's
existing `match_clause`; words of at least three characters lead, longest first,
so the trigram index gets the best stable selectivity proxy available without a
new corpus statistic. All-short queries are still measurable.

**Reaching the trigram index at all (migration 148).** That selectivity ordering
assumed the `gin_trgm_ops` index on `sentences.text` was reachable. Between
migration 018 and migration 148 it was not, for any RLS-subject caller.
PostgreSQL refuses to evaluate a non-leakproof qual ahead of a security qual,
and both pattern operators are non-leakproof (`textlike`, `texticlike`:
`proleakproof = false`) — testing "does this sentence match?" before "may this
caller see it?" could disclose that an unreadable document contains the term. The
planner therefore chose a sequential scan, and continued to under
`enable_seqscan = off`, where it reports the scan carrying the disable penalty
because no alternative exists. Measured on production 2026-07-28: an ordinary
one-word search read all 1,966,114 sentences in 3,774 ms.

`docray_readable_sentence_ids` is the way back to the index. It is `SECURITY
DEFINER`, owned by the `BYPASSRLS` batch writer (the migration 114 invariant),
and applies `docray_readable_documents()` explicitly in its body, so RLS is not
in the planner's way. Its arity is fixed at four scalar patterns because only
separate scalar `ILIKE` terms reach the index — `ILIKE ALL (array)` and an
`unnest` form both fall back to a scan — and unused slots take `%`, which matches
everything and yields no trigram. Passing fewer patterns than a phrase has words
stays correct: this is a candidate superset, and the pure verifier removes the
false positives.

The safety argument is that `SECURITY DEFINER` changes `current_user` but not the
`role` GUC or `session_user`, so `docray_effective_actor()` still resolves the
*caller's* principal rather than the batch role's.
`test_sentence_search_definer_db.py` pins that, along with cross-principal
isolation and the admin path — the guarantee moved out of the database's hands,
so it is asserted rather than assumed. `adapters/sentence_search_sql.py`
decides eligibility: a term whose match plan is an OR of confusable spellings or
a `translate()`-folded column keeps the previous predicate, because reducing an
OR to one branch would drop true matches rather than merely widening the
candidate set. Terms under three characters are also excluded — pg_trgm cannot
extract a trigram from them — which is the one case the fix cannot help, and
already the cheap one because the candidate cap fills immediately.

**Routing the ordered surfaces (BL-188, D-036).** `term_frequency` could adopt
the helper directly, because its bounded scan is a count and an arbitrary capped
subset still yields an honest floor. `searchSentences` could not: it orders by
(document title, sentence ordinal, id) with keyset pagination, and the helper
applies its `LIMIT` *before* any ordering, so capping an unordered set and
sorting the survivors returns a different page than sorting the whole match set
and taking the first one — silently, and only for queries that matched a lot.

The gate is completeness. The probe asks for `cap + 1` rows; at or under the cap
the helper enumerated the entire match set, so ordering it in the outer query is
identical to ordering the table, and the page is exact. Past the cap the subset
is arbitrary and the query falls back to the previous predicate. This buys the
index exactly where the cost was measured: a *rare* phrase is the expensive
class, because the scan must read the whole relation to prove that few rows
match, and those are the queries whose complete match set fits under a cap.

The restriction is applied as `s.id = ANY(...)` rather than by re-running the
pattern inside the definer, and that is deliberate. Plain equality is leakproof
where the pattern operators are not, so the primary key stays reachable to an
ordinary RLS-subject caller: the definer pays for the one thing RLS forbids —
turning a pattern into a bounded set of ids — and hands back something the
normal, RLS-enforced query indexes by itself.
`test_sentence_search_index_usage_db.py` asserts that as a plan.

An *empty* probe falls back rather than restricting to nothing, because empty is
ambiguous: it means "no sentence matches" or it means "this connection resolves
no readable documents". The second is what an RLS-exempt caller produces — one
that the old predicate showed every row — so restricting on it would turn "may
read everything" into "may read nothing" for indexable terms only. The helper's
patterns are a superset in any case (a multi-word term is split into words, and
short, confusable and fold-matched words are dropped), so the original predicate
is always re-applied outside it; on the substring path, which has no verifier,
that is what keeps the answer exact.

The adapter scans
keyset batches, verifies in `domain/phrase_profile.py`, and stops after 2,001
candidates or a full output page. Its debug log records candidates scanned,
verified rows, emitted rows, and whether the cap was reached.

Stored `sentences.text` collapses whitespace, so it cannot be the citation-offset
authority. Candidate generation uses that indexed normalized value, but phrase
verification uses the raw `document_content.full_text` slice addressed by the
Sentence's Span. Absolute half-open occurrence offsets are therefore exact even
for `algorithm␠␠design`; KWIC quotes the first raw verified occurrence. GraphQL
exposes `occurrences`/`occurrenceCount` and a metadata-bearing
`searchSentencePage`; MCP returns `matchProfile: "surface_word_v1"` and candidate
cap evidence in `coverage`; the webapp's Whole phrase toggle uses the same SQL
builder and verifier. No schema, index, token-table, model, or configuration
change is involved.

**How the `termFrequency` verdict tiers compose.** A single indexed lexeme uses
the maintained lexeme rollup and reports `measurement: "exact"`; that tier and
its nullable scan fields are unchanged. A whitespace phrase retains
`measurement: "component_upper_bound"` and the rarest component's saturation
ceiling as secondary evidence. The primary `method: "sentence_scan"` tier now
reuses the phrase-search `phrase_candidate_clause` for multi-word terms and the
single-pattern `match_clause` for one-profile-word compounds such as
`shared_buffers`. One `UNION ALL` statement fetches no more than
`SENTENCE_HIGH_HITS = 2001` candidate Sentence ids/texts per term.

The pure domain verifier then runs `phrase_profile.find_occurrences` on every
candidate. `profile: "surface_word_v1"` names the rule; `sentenceHits` is the
bounded candidate count, `occurrenceSentences` counts candidates with at least
one valid occurrence, and `occurrences` counts every overlapping occurrence.
Consequently `occurrences >= occurrenceSentences <= sentenceHits`. `attested`
and `floodRisk` use `occurrenceSentences`, so `algorithm designed` may be a SQL
candidate for `algorithm design` without inflating the verdict. When `capped` is
true, all three counts are floors because unseen candidates may add matches, and
the verdict is always high/incomplete. A capped verified zero leaves `attested`
null rather than claiming absence from incomplete work. Debug logs distinguish candidates fetched
from verified sentences/occurrences. This adds no schema, index, token table,
model, or configuration.

**Versioned phrase profile.** `domain/phrase_profile.py` defines
`surface_word_v1`, the shared word-boundary contract used by phrase search and
verified term-frequency counts. Unicode letters, digits, and underscore form words;
whitespace plus folded apostrophe/hyphen variants preserve adjacency, while
other punctuation breaks it. Matching reuses `search_terms.fold`, reports exact
offsets into the original text, and counts overlapping starts. Every result must
carry the profile identifier; changing any policy creates a new profile rather
than changing `surface_word_v1` in place.

**KWIC.** `lexeme_kwic` slices a ±45-character window out of `full_text` in SQL
and splits it into `pre`/`hit`/`post` in Python for highlighting, with keyset
pagination over `(title, char_start, annotation_id, span_id)`.

---

## 8. The verification architecture

PALS's Law is not a slogan in this codebase; it is implemented as a specific gate
per model output. This table is the map.

| Model output | Producer | Gate | Failure route |
|---|---|---|---|
| spaCy annotation graph | `annotation_spacy` | `validate_spacy_annotation_output` — pure structural invariants | raise before persistence; nothing is published |
| NER mention types/spans | `annotation_spacy` | `ner_corrections.reconcile` — durable human corrections replayed every run | corrected mention marked `source='human'` |
| Entity-resolution verdict | `resolver_claude` / `resolver_ollama` | `resolver_shared.to_resolution` + `CandidateResolutionVerifier` | coerced to `ABSTAIN` → `resolution_review` |
| VLM image caption | `vlm_ollama` / `vlm_vllm` | `visual_grounding.grounding_score` specificity gate | `status='review'` + `visual_review` queue |
| Metadata suggestion | `document_metadata_suggest` | `MetadataSuggestionVerifier` via `application.verification` | rejected before persistence |
| Seed suggestion | `seed_suggest` | none automatic — all rows land `pending` | human curation before the registry is touched |
| Cross-modal embeddings | `vision_encoder_openclip` | used for **ranking only**; never asserts a fact | n/a |

### 8.1 Constrained choice — the entity resolver

`ports/resolver.py` is the sharpest expression of the principle. The resolver may
return exactly three verdicts: `SELECT` (an id from the offered candidate set),
`NEW` (a genuinely novel entity, with a proposed label), or `ABSTAIN`. It may
never emit a freehand identifier from model memory, because *a selection is
verifiable and a recalled id is not*.

The constraint is enforced three times over:

1. **In the schema.** `resolver_shared.build_schema` builds a JSON schema whose
   `canonical_id` enum literally *is* the candidate id list. With no candidates,
   the field admits only `null`, so the model can only declare new or abstain.
   `new_entity_type` is likewise a closed enum (`PERSON`, `PLACE`, `ORG`, `EVENT`,
   `WORK`, `ANIMAL`, `CONCEPT`, `OTHER`).
2. **In the mapper.** `to_resolution` re-checks the returned id against the
   offered set and coerces anything malformed to `ABSTAIN` — an out-of-set id, a
   boolean masquerading as an int, a `NEW` verdict with no name.
3. **In the policy.** `resolution.resolve_residue` commits only at
   `confidence >= 0.8`; everything else is queued in `resolution_review` with the
   full candidate set preserved for audit.

`_clamped_confidence` deserves a specific mention: it rejects NaN explicitly,
because `min(1.0, nan)` returns `1.0` in Python, and a NaN confidence would
otherwise clamp to *full* confidence and auto-commit — the exact inversion of the
intended policy.

The same `resolver_shared` module backs both the Anthropic and the Ollama
adapters (`DOCRAY_RESOLVER_BACKEND`), so the guarantee cannot be weakened by
swapping backends.

### 8.2 Grounding — the VLM caption gate

`application/visual_grounding.py` scores how much of a caption's *specific*
content (named entities, numerals, distinctive labels — stopwords removed) is
supported by the document's own figure text. A generic caption ("a diagram")
carries almost no groundable specificity and therefore *cannot* clear the
threshold; a specific caption matched against specific evidence can. The scorer
is pure and deterministic, and returns the matched terms and component scores so
a reviewer sees why a caption was trusted. Only `status='committed'` captions are
exposed as trusted output.

---

## 9. Deterministic semantic enrichment

**Where:** `application/semantic_enrichment.py`, run as the `semantic_enrich`
job or the `doc_ray.entrypoints.annotate_semantic` CLI. Tool tag
`doc-ray/semantic-heuristic` v1.0, layer `semantic-heuristic`.

This module fills the schema-backed layers that have a *safe deterministic
fallback*, and it is candid about what those fallbacks are:

| Layer | Table | Mechanism | Honest description |
|---|---|---|---|
| 17 · Discourse | `discourse_relation` | four connectives — `because` → `Contingency.Cause.Reason`, `therefore` → `Contingency.Cause.Result`, `however`/`but` → `Comparison.Contrast` — with arg1/arg2 taken as the sentence text either side of the connective | PDTB-*shaped*, not PDTB parsing; one relation per sentence, first match wins |
| 18 · Temporal | `timex` | regex over ISO dates, bare four-digit years, and English month names | every hit typed `DATE`; no normalization to a value, no TLINK ordering |
| 18 · Events | `event` | every token with `upos == 'VERB'` | every verb becomes an `OCCURRENCE`; no event/state distinction, no factuality |
| 19 · Sentiment | `sentiment` | 7 positive + 7 negative word lexicon, score ±1.0 | token-level lexicon lookup; no negation handling, no target attribution |
| 20 · Embeddings | `embedding` | 768-dim hashed bag-of-words (`hash-bow-768`): blake2b of each casefolded word → bucket index, counted, L2-normalized | **not a learned representation** — no semantic similarity between distinct words |

The embedding entry deserves emphasis: `hash-bow-768` occupies the same
`embedding` table and pgvector index that a learned model would, and it produces
mathematically valid unit vectors — but two synonyms hash to unrelated buckets,
so nearest-neighbour results reflect vocabulary overlap, not meaning. It is a
structural placeholder that keeps the vector plumbing exercised. The genuinely
learned vectors in this system are the CLIP-family ones in
`image_embedding`/`sentence_vision_embedding` (section 11).

Two performance decisions are load-bearing and documented in place: token
surfaces are sliced in Python from one `full_text` fetch rather than with SQL
`substring()` per row (measured 76.5 s → 0.2 s on a 98k-token document, because
`substring()` detoasts the whole text per row), and embeddings are written with
`COPY` rather than `executemany` (measured ~2.7× less database CPU against the
HNSW-indexed column).

The whole run is transactional with replacement semantics and a provenance event,
exactly like the spaCy pass.

---

## 10. Entity canonicalization — the flywheel

**Where:** `application/canonical_seed.py`, `term_scan.py`,
`candidate_generation.py`, `canonicalize.py`, `resolution.py`,
`seed_suggest.py`; `domain/canonical_norm.py`; `adapters/canonical_store.py`,
`wikidata.py`, `wikipedia.py`. Full design in
[`canonicalization_pipeline.md`](canonicalization_pipeline.md).

This is layer 12 (entity linking) implemented as a staged pipeline whose explicit
goal is to *reduce* model usage over time.

**The normalization key.** `canonical_norm.form_norm` is the join between every
stage: NFC + casefold (reusing the lexeme lemma normalization), then ASCII
transliteration via `unidecode`, then a final casefold and whitespace collapse.
`Zelenskyy`, `ZELENSKYY`, `Zélénskyy`, and a transliterated Cyrillic spelling all
land on one `form_index` key.

**Stage 0 — seeding.** `canonical_seed.py` pulls labels, aliases, descriptions,
and types from Wikidata/Wikipedia and writes a `canonical` row plus one
`form_index` row per surface. Idempotent per `wikidata_id`. The `KnowledgeBase`
port is injected, so it is unit-testable without network.

**Stage 1.5 — registry term scan.** `term_scan.py` closes a gap that NER cannot:
in technical literature the domain vocabulary is lowercase common nouns
(`uncial`, `parchment`), which NER never tags, leaving seeded registry entries
unreachable. The scan compiles every known registry surface into one alternation
regex — longest-first so the longest match wins, whitespace-flexible inside
multi-word surfaces, case-insensitive except for all-caps acronym forms which
must match case-sensitively (`NASA`, never `nasa`).

Three filters keep it from flooding documents with false mentions:

- **Eligibility** — a single-token surface shorter than four characters is
  dropped (`BM`, `pen`, `Rom`), as is a surface in a small stopword list of
  common English words that also occur as KB aliases.
- **POS agreement** — a single-token hit whose exactly-aligned token carries a
  non-nominal UPOS is discarded. Terminology is nominal (`NOUN`, `PROPN`, `ADJ`,
  `X`); a hit tagged `VERB` is ordinary usage of a polysemous alias ("a loose
  *end* hanging from…"), not a mention. Multi-word hits are strong enough anchors
  to pass unconditionally, and hits with no token information get the benefit of
  the doubt.
- **Span exclusivity** — hits overlapping a span already claimed by another
  mention layer are suppressed; NER owns those spans.

The scan only *emits* mentions under `layer='term'`; linking is left to Stage 2,
so an ambiguous term still becomes residue rather than being force-linked. Being
registry-driven makes it field-agnostic: seeding a new domain immediately makes
that vocabulary detectable with no model, no retraining, and no LLM cost.

**Stage 2 — blocking.** `candidate_generation.candidates_for_form` normalizes the
surface, looks it up in `form_index`, and keeps the best-confidence row per
distinct canonical. Exactly one candidate at `confidence >= 0.9` is
**auto-linked** and never reaches a model. Anything ambiguous (several
canonicals) or novel (no hit) becomes **residue**.

**Stage 3 — the frontier resolver.** `resolution.resolve_residue` runs only when
the job payload sets `resolve`. Before any call, mentions whose NER type is
non-referential — `CARDINAL`, `ORDINAL`, `DATE`, `TIME`, `PERCENT`, `MONEY`,
`QUANTITY` — are skipped outright: quantities and time expressions are not
referential entities, and calling a model on them is pure cost. Each remaining
mention is sent with its containing sentence as disambiguating context and the
Stage-2 candidate set, under the constrained-choice contract of section 8.1.

**Stage 5 — review.** Abstentions and sub-threshold verdicts are inserted into
`resolution_review` as `pending`, with the offered candidate set stored as JSONB
so a reviewer sees exactly what the model was choosing between.

**Stage 6 — write-back, and why it is a flywheel.** On every committed verdict,
`_write_back` registers the preferred label, the observed surface, and every
alias the resolver harvested into `form_index` with `source_type='corpus'`. The
next occurrence of any of those forms resolves by *index lookup* at Stage 2 — the
frontier model never sees it again. Model cost per entity therefore trends toward
zero as the corpus grows, and the registry accumulates the vocabulary of the
domains it has actually read.

**Seed suggestion.** `seed_suggest.py` automates the mechanical part of domain
seeding from two deterministic candidate sources — surfaces of not-yet-linked
entity mentions (grouped by most frequent casing, non-referential types excluded,
≥3 occurrences), and frequent `NOUN` lemmas from the lexeme layer (≥10
occurrences, the technical-vocabulary channel NER never sees). Candidates already
resolvable through `form_index` are skipped, since the flywheel means a known
form needs no new seed. Each candidate is pinned to an exact Wikidata identity
through its Wikipedia article's `pageprops` — not a fuzzy top search hit. Every
row lands `pending`; judging whether "Albertus" means the typeface or the friar
stays human.

---

## 11. Multimodal and cross-modal language use

Three tiers extend language processing to images. They are NLP-adjacent because
each ties image content back to text.

**Tier 1 — deterministic visual features** (`visual_extract.py`). Detects image
regions and stores geometry and features. CPU-only, no model, which is why it can
sit in the default ingest chain.

**Tier 2 — cross-modal embeddings** (`visual_embed.py`,
`vision_encoder_openclip.py`). A CLIP/SigLIP dual tower embeds image crops
through the vision tower and the document's *own sentences* through the same
model's text tower, into one shared space (`image_embedding`,
`sentence_vision_embedding`). This is what makes text→image search
(`cross_modal_image_search`) and image→image search work, and these are the
system's only genuinely learned text embeddings. Writes are per-model
delete-then-`COPY`, vectors L2-normalized. Configured by `DOCRAY_VISION_MODEL`
(default `ViT-L-14`) and `DOCRAY_VISION_PRETRAINED` (default `openai`). Encoder
output is used for **ranking only** — it never asserts a fact into the graph.

**Tier 3 — grounded captions** (`visual_caption.py`, `visual_grounding.py`,
`vlm_ollama.py` / `vlm_vllm.py`). A vision-language model captions each image;
the caption is then grounded against the document's own figure text and gated as
described in section 8.2.

---

## 12. Read-time reconciliation

Some NLP honesty work happens on the *read* side, not the write side.

**Entity profiles** (`domain/entity_profiles.py`, exposed as GraphQL
`entityProfiles` and MCP `entity_profiles`). Raw NER splits one real-world entity
across types and spellings — in the live corpus `Arrakis` is tagged `LOC` 225×,
`GPE` 150×, `PERSON` 95×, and `NORP` 76×, four rows an agent would otherwise have
to merge by hand. `reconcile` groups frequency rows by `form_norm` — the *same*
key `form_index` uses, so the grouping agrees with the canonical registry rather
than inventing a second notion of sameness — and reports:

- `mentions` — total across every type and spelling in the group;
- `dominant_type` and the full `type_spread`;
- `type_consistency` — the dominant type's share of mentions. This is
  **arithmetic, not a model score**. `Arrakis` scores 0.41, which is the signal
  that the type is contested. A low value means the annotator disagreed with
  itself, not that the entity is unimportant.

The module deliberately merges only normalization-equivalent spellings. `Duncan`
and `Duncan Idaho` stay separate: relating them is coreference, and folding them
here would be fabrication. Note that `limit` is applied *after* grouping —
truncating raw rows first would drop half of a split entity and report a total
that is simply wrong.

**Document outline** (`domain/document_outline.py`, MCP
`read_document_outline`). Extractors normalize detected headings into ATX markers
inside the stored text; PDFs get them from the relative-font-size heuristic in
`domain/pdf_layout.py`. That heuristic is uneven by nature — measured across the
live corpus it finds 723 headings in a technical book, 17 in *Dune*, and none at
all in *Dune Messiah*. Rather than return an empty outline for exactly the
document that needs one, `plan_uniform_buckets` supplies an even split as a
fallback. The two are never conflated: the response reports `source: 'headings'`
or `source: 'uniform'`, and a grid is described as a grid rather than dressed up
as a table of contents.

**Read-model rollups.** Per decision D-024, aggregate reads do not scan fact
tables. `document_stats.n_tokens` and the tenant-scoped
`entity_frequency_stats(tenant_id, entity_text, entity_type, uses)` are
precomputed at pipeline boundaries (migration 100), because
`Document.tokenCount` was a live `count(*)` over 25M token rows and corpus-wide
`entityFrequency` was a `GROUP BY` over 1.1M mentions.

---

## 13. Exposure surfaces

**GraphQL** (`src/doc_ray/entrypoints/api/`) — the layer fields live in
`layer_types.py` and `structure_query.py`: `Document.tokens` (with morphology),
`.chunks`, `.dependencies`, `.corefChains`, `.entityLinks`, plus `Query.lexemes`
/ `lexeme` / `Lexeme.occurrences`, `Query.entities`, `entityFrequency`,
`entityProfiles`, `searchSentences`, `fullTextSearch`, `unresolvedMentions`,
`resolutionReview`, `seedSuggestions`, `entityMentionCorrections`, and the
explicit-document, capped `entitySurfaceOccurrences` preview. All reads are
bounded.

**MCP** (`src/doc_ray/entrypoints/mcp/`) — the agent-facing surface, which fronts
GraphQL rather than the database. NLP-relevant tools: `read_document_outline`,
`read_document_sentences`, `search_sentences`, `full_text_search`,
`entity_profiles`, `entity_frequency`, `entity_dossier`, `lexeme_kwic`,
`briefing_pack`, `document_evidence`, `cross_modal_image_search`,
`visual_captions`, `correct_entity_mention`, `correct_entity_surface`. The
systematic tool defaults to dry-run preview and refuses an unprovably complete
200-member Collection page. The server's instructions steer
agents toward `entity_profiles` over raw `entity_frequency` precisely because of
the type-splitting problem in section 12.

**Webapp** (`webapp/`) — `/corpus` (browse), `/search` (sentence search),
`/lexemes` (the lexeme index with POS filters), `/lexeme/<uid>` (KWIC
concordance), `/doc/<id>` (the reader), `/doc/<id>/seeding` (seed curation). The
reader is where the annotation becomes visible to a human: `reader_blocks.py`
renders each annotated word as a link to its lexeme page, carrying a POS class, a
POS sigil, and an ARIA label of the form "word, lemma X, part of speech Y".

**Standalone tools** (`scripts/`) — independent of the pipeline and of the
database: `docmodel.py` (lossless `document → section → paragraph → sentence →
phrase → word` decomposition with `compose(decompose(t)) == t`),
`extract_phrases.py` (sentence extraction across formats, with mid-sentence line
rejoining for PDF/column layouts), `text_decomposer.py` (the same hierarchy with
an optional NLTK path for sentences and grammatical chunking).

---

## 14. Configuration

| Variable | Default | Effect |
|---|---|---|
| `DOCRAY_SPACY_FALLBACK_POLICY` | `fail` | `fail` = unsupported language raises; `fallback` = annotate with the default language and record the fallback in provenance |
| `DOCRAY_RESOLVER_BACKEND` | `anthropic` | `anthropic` or `ollama`; an unknown value fails closed rather than silently defaulting a typo |
| `DOCRAY_RESOLVER_MODEL` | unset | Overrides the resolver model |
| `DOCRAY_RESOLVER_TIMEOUT_SECONDS` | unset | Resolver call timeout |
| `ANTHROPIC_API_KEY` | unset | Required for the `anthropic` resolver backend |
| `DOCRAY_OLLAMA_HOST` / `DOCRAY_OLLAMA_MODEL` | `http://localhost:11434` / `qwen2.5` | Local resolver and VLM backend |
| `DOCRAY_VISION_MODEL` / `DOCRAY_VISION_PRETRAINED` | `ViT-L-14` / `openai` | Tier-2 cross-modal encoder |
| `DOCRAY_PDF_OCR` | `1` | OCR a PDF that extracts to zero text; `0` records the failure instead |
| `DOCRAY_PDF_OCR_LANGUAGE` / `DOCRAY_VLM_OCR_LANGUAGE` | `eng` | OCR language for scanned pages and image crops (image ships `eng`, `por`) |

The canonical registry is `config/env.registry.yaml`; see
[`environment-configuration.md`](environment-configuration.md).

---

## 15. Quality signals and drift

**Structural drift.** `src/doc_ray/tests/test_nlp_capability_matrix.py` parses
[`nlp_capability_matrix.md`](nlp_capability_matrix.md) and fails if a documented
layer disappears, if a row claims GraphQL exposure that does not resolve to a
live Strawberry field, or if a row claims a writer without a citable production
path and test. It proves the documentation has not drifted from the code; it says
nothing about annotation quality.

**Corpus drift.** `application/evaluation.py` snapshots deterministic corpus
facts — lemma distribution, entity-type distribution, per-tool versions, review
queue counts, schema version — so two runs can be compared. It measures *change*,
not correctness.

**Operator health.** `application/corpus_health.py` raises
`document-unannotated` (sentences exist, no entity mentions — annotation never
ran), `embeddings-missing` (sentences exist, no vectors — vector search
unavailable), and an FTS-config mismatch signal (content indexed with one config
while queries use another).

None of these is an accuracy metric. There is no gold-standard evaluation set in
the repository, so annotation *quality* is currently unmeasured — the guards
verify that the pipeline ran, that its output is structurally valid, and that
what is documented exists.

---

## 16. Known limits

Stated plainly, because the capability matrix's status column is easy to skim
past.

1. **Coreference resolves repeated proper nouns only.** No pronouns, no bridging,
   no nominal descriptions (section 5.3).
2. **Sentiment is a 14-word lexicon** with no negation handling and no target
   attribution.
3. **Discourse covers four connectives**, one relation per sentence.
4. **Temporal annotation types everything `DATE`** with no value normalization and
   no event ordering; every verb is an `OCCURRENCE`.
5. **`hash-bow-768` is not a learned embedding.** Synonyms are unrelated in that
   space (section 9).
6. **Five layers are schema-only** — subword tokenization (4), constituency
   parsing (9), semantic role labeling (14), word-sense disambiguation (15), and
   AMR (16). Tables and integration tests exist; no production writer does.
   Each needs a dedicated tokenizer, parser, model, or knowledge base — there is
   no safe deterministic fallback, and inventing one would be worse than the gap.
7. **Language support is three languages** (`en`, `fr`, `pt`) for annotation and
   **two** (`en`, `pt`) for search stemming; everything else falls to the
   `simple` text-search config (section 7).
8. **Language detection is stopword voting**, which is weak on very short texts
   and on code-heavy or formula-heavy documents.
9. **Sentence-level search does not stem.** `running` will not find `run`
   (section 7).
10. **Annotation quality is unmeasured.** No gold set, no precision/recall
    figures (section 15).

---

## 17. File map

| Concern | Path |
|---|---|
| Sentence segmentation | `src/doc_ray/domain/sentences.py` |
| Language detection, tokenization, lexeme identity, `RuleAnalyzer` | `src/doc_ray/domain/lexemes.py` |
| Normalization key for entity forms | `src/doc_ray/domain/canonical_norm.py` |
| NER correction reconcile (pure) | `src/doc_ray/domain/ner_corrections.py` |
| Entity profile reconciliation (pure) | `src/doc_ray/domain/entity_profiles.py` |
| Document outline / addressable ranges | `src/doc_ray/domain/document_outline.py` |
| Annotator contract | `src/doc_ray/ports/annotator.py` |
| Resolver contract (constrained choice) | `src/doc_ray/ports/resolver.py` |
| spaCy multi-layer writer | `src/doc_ray/adapters/annotation_spacy.py` |
| Model/language registry | `src/doc_ray/adapters/annotation_capabilities.py` |
| Pre-persistence validation gate | `src/doc_ray/adapters/annotation_validation.py` |
| Zero-dependency lexeme writer + CLI | `src/doc_ray/adapters/annotation.py` |
| Frontier resolver (shared logic / backends) | `src/doc_ray/adapters/resolver_shared.py`, `resolver_claude.py`, `resolver_ollama.py`, `resolver_factory.py` |
| Knowledge-base clients | `src/doc_ray/adapters/wikidata.py`, `wikipedia.py` |
| Ingest use case | `src/doc_ray/application/pipeline.py` |
| spaCy lemma/POS refresh | `src/doc_ray/application/reannotate.py` |
| Deterministic semantic layers | `src/doc_ray/application/semantic_enrichment.py` |
| Registry term scan | `src/doc_ray/application/term_scan.py` |
| Blocking / auto-link / resolution / seeding | `src/doc_ray/application/candidate_generation.py`, `canonicalize.py`, `resolution.py`, `canonical_seed.py`, `seed_suggest.py` |
| Job dispatch | `src/doc_ray/application/job_handlers.py`, `jobs.py` |
| Drift snapshots / health | `src/doc_ray/application/evaluation.py`, `corpus_health.py` |
| Read adapters | `src/doc_ray/adapters/persistence_read.py`, `persistence_layers_read.py`, `persistence_read_structure.py`, `persistence_canonical_read.py` |
| Schema | `db/migrations/005`–`012`, `016`, `021`–`023`, `089`–`093`, `100`, `115` |

---

Back to the [project README](../README.md) · [architecture](architecture.md) ·
[capability matrix](nlp_capability_matrix.md)
