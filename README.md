# doc-ray

Document store built on **PostgreSQL** (metadata + full text) and **MinIO / S3**
(original bytes), with a Python ingestion pipeline.

## Architecture

```
             ┌────────────┐   extract text/meta   ┌──────────────────┐
  file(s) ─► │  ingester  │ ────────────────────► │ Postgres         │
  (.pdf)     │  (Python)  │   documents + FTS      │  documents       │
             │            │                        │  document_content│
             └─────┬──────┘                        └──────────────────┘
                   │ put original bytes (content-addressed by sha256)
                   ▼
             ┌────────────┐
             │  MinIO/S3  │  s3://documents/<ab>/<sha256>.pdf
             └────────────┘
```

- **Postgres** holds queryable metadata (`documents`) and extracted text with a
  generated `tsvector` for full-text search (`document_content`). Blobs never
  live in a row — only an `s3://` pointer + sha256 checksum.
- **MinIO** stores the original file bytes, keyed by content hash (dedupe + integrity).
- **ingester** computes the hash, uploads the blob, extracts text/metadata, and
  writes one transaction to Postgres. Re-ingesting identical bytes is a no-op.

## Quick start

```bash
cp .env.example .env

# 1. Bring up Postgres + MinIO (schema auto-applies on first init)
docker compose up -d postgres minio createbucket

# 2. Build the ingester image
docker compose build ingester

# 3. Ingest the sample PDF (repo root is mounted at /data)
docker compose run --rm ingester /data/orwell1984.pdf

# ...or a whole directory, recursively
docker compose run --rm ingester /data
```

Expected output:

```
Ingesting 1 file(s) into postgres:5432/docray ...
  [OK ] orwell1984.pdf -> 3f2a...uuid
Done: ingested=1
```

## Verify

```bash
# Row landed with page count + status
docker compose exec postgres psql -U docray -d docray \
  -c "SELECT title, doc_type, page_count, status, source_uri FROM documents;"

# Full-text search over extracted content
docker compose exec postgres psql -U docray -d docray -c \
  "SELECT d.title, ts_rank(c.fts, q) AS rank
     FROM document_content c
     JOIN documents d ON d.id = c.document_id,
          plainto_tsquery('english', 'Big Brother') q
    WHERE c.fts @@ q
    ORDER BY rank DESC;"

# Metadata / structured query (your primary access pattern)
docker compose exec postgres psql -U docray -d docray -c \
  "SELECT title, author, metadata->>'pdf_producer' AS producer
     FROM documents WHERE doc_type = 'pdf' ORDER BY created_at DESC;"
```

MinIO console: http://localhost:9001 (login from `.env`).

## Running the ingester on the host (no docker for Python)

```bash
pip install -r requirements.txt
# DATABASE_URL / S3_ENDPOINT default to localhost — see .env.example
python -m doc_ray.entrypoints.cli orwell1984.pdf
```

## Layout

| Path                       | Purpose                                            |
|----------------------------|----------------------------------------------------|
| `docker-compose.yml`       | Postgres + MinIO + bucket-init + ingester services |
| `db/migrations/`           | Schema, indexes, triggers (auto-applied)           |
| `src/doc_ray/`             | The core package — **hexagonal / DDD** layout (below) |
| `src/doc_ray/domain/`      | Pure logic & value objects: `pdf_layout`, `sentences`, `lexemes`, `ulid`, `provenance` |
| `src/doc_ray/ports/`       | Boundary contracts: `TextExtractor`, `ObjectStore`, `Repository`, `Annotator` |
| `src/doc_ray/adapters/`    | Driven adapters: `extraction` (PyMuPDF/pypdf/…), `persistence` (Postgres), `object_store` (MinIO), `annotation`/`annotation_spacy` |
| `src/doc_ray/application/` | Use cases: `pipeline`, `reannotate`, `reextract`   |
| `src/doc_ray/entrypoints/` | Driving adapters: `cli` (`python -m doc_ray.entrypoints.cli`) |
| `src/doc_ray/config.py`    | Env-driven configuration                           |
| `webapp/`                  | Flask browser over the annotated corpus (frontend, kept **outside** `src/`) |
| `scripts/`                 | Standalone text tools (`extract_phrases`, `docmodel`, `roundtrip`, `text_decomposer`) — independent of the pipeline; see `scripts/README.md` |
| `docs/`                    | Design notes & specs; see [`docs/architecture.md`](docs/architecture.md) |

## Architecture Decisions

Architectural and operational decisions are recorded, not just narrated, in
[`engineering_decisions.yml`](engineering_decisions.yml) — a machine-checkable log
validated by the SHACL/YAML toolchain vendored under
[`engineering/schema/`](engineering/schema/). Validate and project it with:

```bash
python engineering/schema/scripts/validate_decisions.py engineering_decisions.yml   # fast YAML gate
python engineering/schema/scripts/yml_to_rdf.py engineering_decisions.yml > /tmp/ed.ttl
python engineering/schema/scripts/shacl_validate.py /tmp/ed.ttl                      # full SHACL gate
```

Current decisions (see the record for rationale, claims, and auditable checks):

| ID | Decision |
|----|----------|
| D-001 | Expose the store through a GraphQL (Strawberry) API |
| D-002 | Serve reads through a `ReadRepository` port + async adapter |
| D-003 | Preserve autocommit + statement-level lexeme collision-grow |
| D-004 | Background jobs via a Postgres queue (`FOR UPDATE SKIP LOCKED`) |
| D-005 | Sync worker for blocking work; async only for API reads |
| D-006 | Runtime migration runner closes the init-only gap |
| D-007 | `jobs` table: text+CHECK status, open text kind (migration 019) |
| D-008 | GraphQL safety: depth + complexity limits, keyset pagination |
| D-009 | Pluggable extractor & analyzer registries |
| D-010 | Access control via Postgres RLS; API sets the principal |
| D-011 | Ruff strict lint incl. docstrings, pinned in pyproject |
| D-012 | mypy runs in strict mode |
| D-013 | Test coverage stays at or above 90% |
| D-014 | Gated pre-commit runs the full quality bar |
| D-015 | Every source file stays under 400 net lines of code |
| D-016 | `engineering_decisions.yml` is the ADR system of record |

## Quality gates

Enforced by [`.pre-commit-config.yaml`](.pre-commit-config.yaml) and CI
(`.github/workflows/ci.yml`), configured in [`pyproject.toml`](pyproject.toml):

```bash
pip install -r requirements-dev.txt && pre-commit install
ruff check src scripts webapp                                   # D-011 (lint + docstrings)
mypy src                                                        # D-012 (strict)
pytest --cov=doc_ray --cov-fail-under=90 tests src/doc_ray/tests # D-013 (>=90%)
python scripts/check_file_size.py src/doc_ray webapp scripts    # D-015 (<=400 net LOC)
```

## Extending

- **New file types**: add an extractor to `src/doc_ray/adapters/extraction.py`
  and register it in `_EXTRACTORS` (e.g. `.docx`, `.html`, `.txt`).
- **Semantic / RAG search**: add `CREATE EXTENSION vector;` plus a
  `document_chunks(document_id, chunk_text, embedding vector(N))` table with an
  HNSW index — no migration off Postgres.
```
