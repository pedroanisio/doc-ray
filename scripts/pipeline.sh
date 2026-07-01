#!/usr/bin/env bash
# doc-ray — end-to-end ingestion pipeline (one command).
#
# Ingests ANY document format (pdf / txt / md / html / epub / docx) and produces
# the complete, REAL spaCy NLP mapping: tokens, lemma, UPOS/XPOS, morphology
# (FEATS), noun chunks, dependencies, named entities, coreference, and the
# lexeme-POS layer the webapp renders.
#
#   scripts/pipeline.sh [container-path]      # default: /data/static/fixtures
#
# Stage 1 (extract -> object store -> sentences -> provenance) runs in the
# ingester container. Stage 2 (real spaCy annotation) runs in the host venv,
# which has the spaCy models. Both are idempotent; re-running replaces per doc.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET="${1:-/data/static/fixtures}"
DB="${DATABASE_URL:-postgresql://docray:docray@localhost:5455/docray}"

echo "==> [1/2] Ingest (any format): extract -> store -> sentences -> provenance"
docker compose run --rm ingester "$TARGET"

echo "==> [2/2] Annotate (real spaCy): tokens/POS/morph/deps/NER/chunks + lexeme POS"
DATABASE_URL="$DB" .venv/bin/python -m ingest.annotate_spacy --all

echo "==> Pipeline complete."
