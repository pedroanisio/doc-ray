---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI GPT-5 via Codex"
  date: "2026-07-04"
---

# NLP Capability Matrix

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

This matrix is the drift guard for the NLP surface. It separates schema support
from production writers, read adapters, GraphQL exposure, browser exposure,
operator health, and tests. A layer is not production-exposed just because a
table exists.

Status values:

- `api-exposed`: queryable through the supported GraphQL read surface.
- `ui-exposed`: visible through the browser surface but not necessarily as a layer API.
- `writer-backed`: populated by production pipeline code but not fully exposed.
- `schema-only`: schema and integration tests exist, but no production writer or API.
- `planned-only`: documented target only.

Provenance note: as of `BL-018`, the spaCy-backed production layers
(tokenization, lemmatization/POS, morphology, chunks, dependencies, NER, and
coreference) share one transactionally written `annotated` provenance event in
`document_provenance`, with normalized `provenance_params` for language, tool
version, annotation ids, per-layer counts, lexeme-occurrence counts, and
replacement semantics. `src/doc_ray/tests/test_complete_mapping.py` guards both
the success event and rollback behavior.

Language/model note: as of `BL-022`,
`src/doc_ray/adapters/annotation_spacy.py::spacy_capabilities` is the runtime
registry for spaCy annotation languages. It records language code, model package,
installed status, supported production layers, and the active fallback policy.
Unsupported languages fail clearly by default; operators can opt into explicit
fallback with `DOCRAY_SPACY_FALLBACK_POLICY=fallback`, and the resulting
detected language, annotation language, model, and fallback decision are recorded
in provenance params.

Validation note: as of `BL-019`, spaCy-backed annotation rows pass a pure
structural validation gate before persistence. The validator checks span bounds,
token ordinals, sentence references, dependency heads, entity/chunk/coreference
spans, lexeme projection parity, and language/model support. Successful runs
record `validation_status=passed` and `validation_errors=0` in job and
provenance output; invalid graphs fail before the annotation/provenance rows
become visible.

## Capability Matrix

| Layer | Capability | Schema | Writer | Read adapter | GraphQL | Browser | Health | Tests | Status |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Document identity and content | `db/migrations/001_init.sql`; `docs/DATABASE_SPEC.md` | writer: `src/doc_ray/adapters/persistence.py::insert_document` | `src/doc_ray/adapters/persistence_read.py::list_documents`; `src/doc_ray/adapters/persistence_read.py::get_document` | field: `Query.documents`; `Query.document` | `webapp/app.py` document list and detail pages | `src/doc_ray/application/corpus_health.py` document totals | `src/doc_ray/tests/test_application_pipeline.py`; `src/doc_ray/tests/test_api.py` | api-exposed |
| 2 | Sentence segmentation | `db/migrations/002_sentences.sql`; `db/migrations/007_spans.sql` | writer: `src/doc_ray/adapters/persistence.py::insert_document` | `src/doc_ray/adapters/persistence_read.py::list_sentences`; `src/doc_ray/adapters/persistence_read.py::search_sentences` | field: `Document.sentences`; `Query.search_sentences` | `webapp/app.py` reader and search pages | `src/doc_ray/application/corpus_health.py` sentence totals | `tests/test_sentences.py`; `src/doc_ray/tests/test_api.py` | api-exposed |
| 3 | Tokenization | `db/migrations/008_tokens_morphology.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_build_tokens` | `src/doc_ray/adapters/persistence_read.py::token_counts_by_documents` | field: `Document.token_count` | none: browser has lexeme/KWIC views, not token rows | `src/doc_ray/application/corpus_health.py` token totals | `src/doc_ray/tests/test_008_tokens.py`; `src/doc_ray/tests/test_complete_mapping.py` | api-exposed |
| 4 | Subword tokenization | `db/migrations/008_tokens_morphology.sql` | none: no production subword writer is registered | none: no read-adapter function for subword rows | none: no GraphQL subword field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_008_tokens.py` | schema-only |
| 5 | Lemmatization | `db/migrations/005_lexemes.sql`; `db/migrations/008_tokens_morphology.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_build_tokens` | `src/doc_ray/adapters/persistence_read.py::list_lexemes`; `src/doc_ray/adapters/persistence_read.py::lexeme_kwic` | field: `Query.lexemes`; `Query.lexeme`; `Lexeme.occurrences` | `webapp/app.py` lexeme and KWIC pages | `src/doc_ray/application/corpus_health.py` lexeme totals | `src/doc_ray/tests/test_lexemes.py`; `src/doc_ray/tests/test_complete_mapping.py`; `src/doc_ray/tests/test_api.py` | api-exposed |
| 6 | Part-of-speech tagging | `db/migrations/008_tokens_morphology.sql`; `db/migrations/005_lexemes.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_build_tokens` | `src/doc_ray/adapters/persistence_read.py::list_lexemes` | field: `Query.lexemes`; `Query.lexeme` | `webapp/app.py` POS filters and annotation styling | `src/doc_ray/application/corpus_health.py` annotation coverage | `src/doc_ray/tests/test_complete_mapping.py`; `src/doc_ray/tests/test_api.py` | api-exposed |
| 7 | Morphological features | `db/migrations/008_tokens_morphology.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_build_tokens` | none: no read-adapter function for token_feature rows | none: no GraphQL morphology field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_008_tokens.py`; `src/doc_ray/tests/test_complete_mapping.py` | writer-backed |
| 8 | Shallow chunking | `db/migrations/009_syntax.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_copy_layers` | none: no read-adapter function for chunk rows | none: no GraphQL chunk field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_009_syntax.py`; `src/doc_ray/tests/test_complete_mapping.py` | writer-backed |
| 9 | Constituency parsing | `db/migrations/009_syntax.sql` | none: no production constituency writer is registered | none: no read-adapter function for constituent rows | none: no GraphQL constituent field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_009_syntax.py` | schema-only |
| 10 | Dependency parsing | `db/migrations/009_syntax.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_copy_layers` | none: no read-adapter function for dependency rows | none: no GraphQL dependency field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_009_syntax.py`; `src/doc_ray/tests/test_complete_mapping.py` | writer-backed |
| 11 | Named entity recognition | `db/migrations/010_reference.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_copy_layers` | `src/doc_ray/adapters/persistence_read.py::list_entities`; `src/doc_ray/adapters/persistence_read.py::entity_frequency` | field: `Query.entities`; `Query.entity_frequency`; `Document.entities` | `webapp/app.py` entity and document views | `src/doc_ray/application/corpus_health.py` entity totals | `src/doc_ray/tests/test_010_reference.py`; `src/doc_ray/tests/test_api.py`; `src/doc_ray/tests/test_complete_mapping.py` | api-exposed |
| 12 | Entity linking | `db/migrations/010_reference.sql`; `db/migrations/021_canonical.sql`; `db/migrations/023_canonical_review.sql` | writer: `src/doc_ray/application/term_scan.py` | none: entity-link review reads are not in persistence_read.py yet | none: no GraphQL entity-link field | none: review surfaces are backlog work | `src/doc_ray/application/corpus_health.py` unresolved-link signals | `src/doc_ray/tests/test_term_scan.py`; `src/doc_ray/tests/test_canonicalize_autolink.py` | writer-backed |
| 13 | Coreference | `db/migrations/010_reference.sql` | writer: `src/doc_ray/adapters/annotation_spacy.py::_build_entities` | none: no read-adapter function for coref rows | none: no GraphQL coreference field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_010_reference.py`; `src/doc_ray/tests/test_complete_mapping.py` | writer-backed |
| 14 | Semantic role labeling | `db/migrations/011_meaning_discourse.sql` | none: no production SRL writer is registered | none: no read-adapter function for SRL rows | none: no GraphQL SRL field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_011_meaning.py` | schema-only |
| 15 | Word-sense disambiguation | `db/migrations/011_meaning_discourse.sql` | none: no production WSD writer is registered | none: no read-adapter function for word_sense rows | none: no GraphQL WSD field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_011_meaning.py` | schema-only |
| 16 | AMR semantic parsing | `db/migrations/011_meaning_discourse.sql` | none: no production AMR writer is registered | none: no read-adapter function for AMR rows | none: no GraphQL AMR field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_011_meaning.py` | schema-only |
| 17 | Discourse relations | `db/migrations/011_meaning_discourse.sql` | none: no production discourse writer is registered | none: no read-adapter function for discourse rows | none: no GraphQL discourse field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_011_meaning.py` | schema-only |
| 18 | Temporal and event annotation | `db/migrations/011_meaning_discourse.sql` | none: no production TimeML writer is registered | none: no read-adapter function for temporal rows | none: no GraphQL temporal field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_011_meaning.py` | schema-only |
| 19 | Sentiment | `db/migrations/011_meaning_discourse.sql` | none: no production sentiment writer is registered | none: no read-adapter function for sentiment rows | none: no GraphQL sentiment field | none: not browser-exposed | none: no health signal beyond table tests | `src/doc_ray/tests/test_011_meaning.py` | schema-only |
| 20 | Embeddings | `db/migrations/012_embeddings.sql`; `db/migrations/021_canonical.sql` | none: no production embedding writer is registered | none: no read-adapter function for embedding rows | none: no GraphQL embedding field | none: embedding backend is not surfaced | `src/doc_ray/application/corpus_health.py` missing-embedding issue | `src/doc_ray/tests/test_012_embeddings.py`; `src/doc_ray/tests/test_corpus_health.py` | schema-only |

## Drift Guard

`src/doc_ray/tests/test_nlp_capability_matrix.py` parses the table above. It
fails if:

- any of the 20 documented NLP layers is missing;
- a row marked GraphQL-exposed does not resolve to a live Strawberry field;
- a row marked writer-backed does not cite a live production path and test; or
- BL-016 is marked done without this matrix.

This is intentionally a static guard. It does not prove semantic quality of an
annotation backend; it prevents documentation from implying that a schema-only
or writer-only layer is already a supported API/browser capability.
