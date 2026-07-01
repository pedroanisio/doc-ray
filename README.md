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
| `db/migrations/001_init.sql` | Schema, indexes, triggers (auto-applied)         |
| `ingest/config.py`         | Env-driven configuration                           |
| `ingest/storage.py`        | S3/MinIO blob store                                |
| `ingest/db.py`             | psycopg data-access layer                          |
| `ingest/extract.py`        | Per-type text/metadata extraction (PDF today)      |
| `ingest/pipeline.py`       | hash → store → extract → persist orchestration     |
| `ingest/cli.py`            | Command-line entrypoint                            |
| `webapp/`                  | Flask browser over the annotated corpus (reads Postgres) |
| `scripts/`                 | Standalone text tools (`extract_phrases`, `docmodel`, `roundtrip`, `text_decomposer`) — independent of the ingest pipeline; see `scripts/README.md` |

## Extending

- **New file types**: add an extractor to `ingest/extract.py` and register it in
  `_EXTRACTORS` (e.g. `.docx`, `.html`, `.txt`).
- **Semantic / RAG search**: add `CREATE EXTENSION vector;` plus a
  `document_chunks(document_id, chunk_text, embedding vector(N))` table with an
  HNSW index — no migration off Postgres.
```
