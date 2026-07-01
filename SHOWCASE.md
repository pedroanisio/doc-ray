# doc-ray — Ingestion Pipeline & Capability Showcase

This document describes the full ingestion tool-chain and how it populates the
complete NLP annotation stack (migrations 001–012) over the `static/fixtures`
corpus.

## The ingesting tools

Every tool is `DATABASE_URL`-driven and idempotent (re-running a layer replaces
that document's run for that tool). They share the stand-off model: text is
addressed by character spans, each run is scoped by `(document, layer, tool)`.

| Tool | Command | Populates | NLP layers |
|---|---|---|---|
| **Base ingest** | `python -m ingest.cli <path>` (in the container) | `documents`, object storage, `document_content`, `sentences`, `document_provenance` | 1, 2 |
| **Lexemes (rule)** | `python -m ingest.annotate --all` | `annotations(layer=lexeme)`, `lexemes`, `lexeme_occurrences` | 3, 5 |
| **spaCy** | `python -m ingest.annotate_spacy --all` | `tokens` (+UPOS/XPOS/lemma link), `token_feature`, `chunk`, `dependency`, `entity_mention`, `coref_chain/mention` | 3, 5, 6, 7, 8, 10, 11, 13 |
| **Embeddings** | `python -m ingest.embed --all` | `embedding` (hash-BoW, pgvector) | 20 |
| **Heuristics** | `python -m ingest.annotate_heuristic --all` | `discourse_relation`, `timex`, `sentiment` | 17, 18, 19 |
| **Export** | `python -m ingest.export --filename <f>` | (read-back) full JSON record | — |
| **Roundtrip** | `roundtrip.py <pdf> <json>` | (verify) reproduces source | — |

## Running the whole pipeline

```bash
# 1. base ingest of every fixture (dedupe skips already-ingested)
docker compose run --rm ingester /data/static/fixtures

# 2. annotation layers (host venv with spaCy + DATABASE_URL)
export DATABASE_URL='postgresql://docray:docray@localhost:5455/docray'
python -m ingest.annotate         --all      # rule lexemes
python -m ingest.annotate_spacy   --all      # tokens/POS/morph/deps/NER/chunks/coref
python -m ingest.embed            --all      # pgvector embeddings
python -m ingest.annotate_heuristic --all    # discourse/temporal/sentiment

# 3. showcase report
docker compose exec -T postgres psql -U docray -d docray -f - < scripts/showcase.sql
```

## Layer coverage

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
| ✅ | 13 coreference | string-match over proper nouns | heuristic |
| ✅ | 17 discourse | PDTB connective regex | heuristic |
| ✅ | 18 temporal (TIMEX) | date regex | heuristic |
| ✅ | 19 sentiment | polarity lexicon | heuristic |
| ✅ | 20 embeddings | feature-hashing BoW → pgvector | real (lexical) |
| ⚪ | 4 subwords, 9 constituency, 12 entity-linking, 14 SRL, 15 WSD, 16 AMR | — | schema ready; needs a dedicated model/KB |

The empty layers have **tables and tests in place** (migrations 008–012); they
await a model or knowledge base — no schema change is required to fill them.

## Honesty notes

- spaCy layers (6, 7, 8, 10, 11) are genuine model output (en_core_web_sm /
  pt_core_news_sm). Coreference, discourse, temporal, and sentiment are
  explicitly **heuristic**, tagged with `heuristic/*` or string-match tools so
  they never masquerade as model output.
- Embeddings are a real, reproducible **lexical** (feature-hashing) representation
  — the model tag `hash-bow@768` says so; they are not neural sentence vectors.
- Multiple annotation runs coexist per document (rule lexemes + spaCy + several
  heuristics), each attributed to its tool — the layer-aware `annotations` design.
