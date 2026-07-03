# Architecture

`doc-ray` is organized as a **hexagonal (ports & adapters)** application with
**DDD** layering. The importable core lives under `src/doc_ray/`; the frontend
(`webapp/`) and standalone tools (`scripts/`) sit **outside** `src/`.

```
                          driving adapter
                        ┌────────────────┐
                        │  entrypoints   │   python -m doc_ray.entrypoints.cli
                        │     (cli)      │
                        └───────┬────────┘
                                │ calls use-cases
                        ┌───────▼────────┐
                        │  application   │   pipeline · reannotate · reextract
                        │   (use cases)  │
                        └───────┬────────┘
             depends on ports   │   (interfaces, not concretions)
                        ┌───────▼────────┐
                        │     ports      │   TextExtractor · ObjectStore
                        │  (contracts)   │   Repository · Annotator
                        └───────┬────────┘
              implemented by    │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                        ▼
┌───────────────┐      ┌────────────────┐       ┌────────────────┐
│   adapters    │      │    adapters    │       │    adapters    │   driven adapters
│  extraction   │      │  persistence   │       │  object_store  │
│ (PyMuPDF/…)   │      │  (Postgres)    │       │   (MinIO/S3)   │
└───────┬───────┘      └────────────────┘       └────────────────┘
        │ uses
┌───────▼──────────────────────────────────────────────────────┐
│  domain  —  pure, dependency-free: pdf_layout, sentences,      │
│             lexemes, ulid, provenance                          │
└───────────────────────────────────────────────────────────────┘
```

**The dependency rule:** dependencies point inward. `domain` depends on nothing;
`adapters` depend on `domain`; `application` orchestrates via `ports`;
`entrypoints` drive the application. Nothing inward depends on a framework.

## Layers

| Layer | Package | Contents | May import |
|---|---|---|---|
| **Domain** | `doc_ray.domain` | `pdf_layout` (layout→Markdown), `sentences` (segmentation), `lexemes` (identity, tokenization, `RuleAnalyzer`, language detection), `ulid` (value object), `provenance` (events) | stdlib only |
| **Ports** | `doc_ray.ports` | `TextExtractor`, `ObjectStore`, `Repository`, `Annotator` — `Protocol` contracts | domain |
| **Adapters** | `doc_ray.adapters` | `extraction` (PyMuPDF + pypdf + txt/html/epub/docx), `persistence` (Postgres/psycopg), `object_store` (MinIO/boto3), `annotation` / `annotation_spacy`, `export` | domain, ports |
| **Application** | `doc_ray.application` | `pipeline` (ingest use case), `reannotate`, `reextract`, `backup` (corpus snapshot & restore) | ports, adapters, domain |
| **Entrypoints** | `doc_ray.entrypoints` | `cli` | application |
| **Config** | `doc_ray.config` | env-driven `Config` (shared kernel) | stdlib |

## Outside the hexagon

- **`webapp/`** — the Flask frontend. Reads Postgres directly; imports **nothing**
  from `doc_ray`. Intentionally kept outside `src/`.
- **`scripts/`** — standalone CLI tools (`extract_phrases`, `docmodel`, …),
  independent of the pipeline.

## Ports & their adapters

| Port | Adapter |
|---|---|
| `TextExtractor` | `adapters.extraction.extract` — dispatch by extension; PDF via PyMuPDF (structured Markdown) with a pypdf fallback |
| `ObjectStore` | `adapters.object_store.ObjectStore` — MinIO/S3 blob store |
| `Repository` | `adapters.persistence` — Postgres data-access (documents, spans, sentences, lexemes, annotations) |
| `Annotator` | `domain.lexemes.RuleAnalyzer` (zero-dep) and `adapters.annotation_spacy.SpacyAnnotator` (real POS) |

The ports are `runtime_checkable` `Protocol`s, so adapters conform structurally —
no subclassing, and a substitute (e.g. a different object store or a math-aware
extractor) drops in without touching the domain or application.

## Backup & restore

A corpus snapshot is **app-native**, not a `pg_dump` shell-out — the worker image
carries no Postgres client. `application.backup` dumps every corpus table with
binary `COPY ... TO STDOUT (FORMAT binary)` (round-tripping `tsvector`,
`vector(768)`, `jsonb`, and array columns without a text re-encode) and bundles it
with every object-store blob into one `.tar.gz` archive plus a JSON manifest.
Restore replays the archive under `SET LOCAL session_replication_role = replica`,
so foreign keys and the full-text-search trigger defer and the dumped tsvectors
survive verbatim, then re-uploads the blobs.

Both directions run as worker jobs (`backup`, `restore`) enqueued by the
`createBackup` / `restoreBackup` GraphQL mutations — the same single write path as
every other mutation. The archive is streamed to clients through a plain
`GET /backups/{id}/download` route on the API, which the webapp proxies so the
browser tier never touches the object store directly. The operational `jobs`
queue and the `backups` catalog are excluded from every archive, so a restore
never clobbers the live queue or the index it is reading from.

## Running

```bash
# tests use pyproject's pythonpath (src, ., scripts) — no install needed
python -m pytest tests/                 # session suite
python -m pytest src/doc_ray/tests/     # NLP-layer suite

# CLI (host): src/ must be importable
PYTHONPATH=src python -m doc_ray.entrypoints.cli <file-or-dir>
# in Docker the image sets PYTHONPATH=/app/src
```
