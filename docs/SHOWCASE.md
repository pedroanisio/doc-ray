---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-04"
---

# doc-ray — Ingestion Pipeline & Capability Showcase

This document describes the full ingestion tool-chain and how it populates the
complete NLP annotation stack (migrations 001–012; entity linking, layer 12,
is covered separately below — it shipped later, against a live KB rather
than the original migrations' schema-only scope) over the `static/fixtures`
corpus.

## The ingesting tools

Every tool is `DATABASE_URL`-driven and idempotent (re-running a layer replaces
that document's run for that tool). They share the stand-off model: text is
addressed by character spans, each run is scoped by `(document, layer, tool)`.

| Tool | Command | Populates | NLP layers |
|---|---|---|---|
| **Base ingest** | `python -m doc_ray.entrypoints.cli <path>` (or `docker compose run --rm ingester <path>`) | `documents`, object storage, `document_content`, `sentences`, `document_provenance` | 1, 2 |
| **Lexemes (rule)** | `python -m doc_ray.entrypoints.annotate --all` | `annotations(layer=lexeme)`, `lexemes`, `lexeme_occurrences` | 3, 5 |
| **spaCy** | `python -m doc_ray.entrypoints.annotate_spacy --all` | `tokens` (+UPOS/XPOS/lemma link), `token_feature`, `chunk`, `dependency`, `entity_mention`, `coref_chain/mention` | 3, 5, 6, 7, 8, 10, 11, 13 |
| **Term scan** | worker job `canonicalize` (`application/term_scan.py`) | `entity_mention` (`layer=term`) for registry-known vocabulary NER never tags | 11 (deterministic) |
| **Entity canonicalization** | `seed_suggest`/`seed_approved`/`canonicalize` worker jobs, or the webapp's `/doc/<id>/seeding` screen | `canonical`, `form_index`, `entity_link` (`scheme=canonical`), `resolution_review` | 12 |
| **Embeddings** | _not yet ported to `doc_ray`_ | `embedding` (hash-BoW, pgvector) | 20 |
| **Heuristics** | _not yet ported to `doc_ray`_ | `discourse_relation`, `timex`, `sentiment` | 17, 18, 19 |
| **Export** | `python -m doc_ray.entrypoints.export --filename <f>` | (read-back) full JSON record | — |
| **Reannotate (spaCy)** | `python -m doc_ray.entrypoints.reannotate --all` | re-runs the spaCy layer for stored documents | 6–11 |
| **Roundtrip** | `python scripts/roundtrip.py <pdf> <json>` | (verify) reproduces source | — |

## Running the whole pipeline

```bash
# 1. base ingest of every fixture (dedupe skips already-ingested)
docker compose run --rm ingester /data/static/fixtures

# 2. annotation layers (host venv with spaCy + DATABASE_URL)
export DATABASE_URL='postgresql://docray:docray@localhost:5455/docray'
python -m doc_ray.entrypoints.annotate        --all   # rule lexemes
python -m doc_ray.entrypoints.annotate_spacy  --all   # tokens/POS/morph/deps/NER/chunks/coref
# embeddings + heuristic (discourse/temporal/sentiment) annotators are not yet
# ported to doc_ray — their tables and tests remain (migrations 011–012).

# 3. showcase report
docker compose exec -T postgres psql -U docray -d docray -f - < scripts/showcase.sql
```

## Layer coverage

This table tracks whether a layer is populated by a real annotator (writer
provenance). For the fuller picture — schema, read adapter, GraphQL,
browser, and health exposure per layer, plus a drift guard that keeps the
claims honest — see [`docs/nlp_capability_matrix.md`](nlp_capability_matrix.md).

| Provenance | NLP layer | Tool | Kind |
|---|---|---|---|
| ✅ | 1 physical structure | base ingest | real |
| ✅ | 2 sentences | base ingest | real |
| ✅ | 3 tokens | spaCy / rule | real |
| ✅ | 5 lemma | spaCy / rule | real |
| ✅ | 6 POS (UPOS/XPOS) | spaCy | real (model) |
| ✅ | 7 morphology (FEATS) | spaCy | real (model) |
| ✅ | 8 chunking | spaCy noun-chunks | real (model) |
| ✅ | 10 dependency | spaCy parser | real (model) |
| ✅ | 11 NER | spaCy | real (model) |
| ✅ | 13 coreference | string-match over proper nouns (spaCy layer) | heuristic |
| ✅ | 12 entity linking | registry-driven term scan + Wikidata-backed canonicalization pipeline (`canonical_seed`/`term_scan`/`canonicalize`/`resolution`) | real (live KB), opt-in frontier LLM for residue |
| ⚪ | 17 discourse | annotator not yet ported to `doc_ray` | schema + tests ready |
| ⚪ | 18 temporal (TIMEX) | annotator not yet ported to `doc_ray` | schema + tests ready |
| ⚪ | 19 sentiment | annotator not yet ported to `doc_ray` | schema + tests ready |
| ⚪ | 20 embeddings | annotator not yet ported to `doc_ray` | schema + tests ready |
| ⚪ | 4 subwords, 9 constituency, 14 SRL, 15 WSD, 16 AMR | — | schema ready; needs a dedicated model/KB |

The empty layers have **tables and tests in place** (migrations 008–012); they
await either a model/knowledge base (4, 9, 14, 15, 16) or a port of the
original heuristic/embedding annotators (17–20). No schema change is required to
fill them. Layer 12 (entity linking) is no longer in this category — see below.

## Honesty notes

- spaCy layers (6, 7, 8, 10, 11) are genuine model output (en_core_web_sm /
  pt_core_news_sm). Coreference (13) is explicitly **heuristic** (string-match
  over proper nouns), tagged so it never masquerades as model output.
- Entity linking (12) resolves against a **live KB** (Wikidata), not a fixed
  offline gazetteer: `canonical_seed` seeds identities by QID or the curated
  `seed_suggest` webapp workflow, `term_scan` + `canonicalize` (Stage-2
  auto-link) resolve deterministically via the remissive index — no model call
  — and only the residue that index lookup can't resolve optionally reaches a
  constrained-choice frontier LLM (Claude), whose adapter validates every
  verdict against the offered candidate set before it can commit (PALS,
  `CLAUDE.md`). Ambiguous or low-confidence verdicts land in a review queue,
  never silently guessed. Full design: `canonicalization_pipeline.md`.
- The discourse (17), temporal (18), sentiment (19), and embedding (20)
  annotators from the original baseline are **not yet ported to `doc_ray`** —
  their tables and tests remain, but no CLI currently populates them, so those
  layers are unpopulated in a fresh run.
- Multiple annotation runs coexist per document (rule lexemes + spaCy), each
  attributed to its tool — the layer-aware `annotations` design.
