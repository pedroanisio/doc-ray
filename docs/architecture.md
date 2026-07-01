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
| **Application** | `doc_ray.application` | `pipeline` (ingest use case), `reannotate`, `reextract` | ports, adapters, domain |
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

## Running

```bash
# tests use pyproject's pythonpath (src, ., scripts) — no install needed
python -m pytest tests/                 # session suite
python -m pytest src/doc_ray/tests/     # NLP-layer suite

# CLI (host): src/ must be importable
PYTHONPATH=src python -m doc_ray.entrypoints.cli <file-or-dir>
# in Docker the image sets PYTHONPATH=/app/src
```
