# Backlog

Deferred work, captured so it isn't lost. Nothing here blocks current use.

## Extraction / ingestion
- **OCR for image-only PDFs.** One fixture (*"Verified Coverage of the FrontierMath
  Ramsey Book-Graph…"*) extracts to **0 characters** — it's scanned/image-only, so it
  has no sentences or annotations. Needs an OCR pass (e.g. Tesseract / `ocrmypdf`)
  before the text extractor.
- **Math fidelity.** PyMuPDF fixes spacing/structure but does **not** recover math as
  LaTeX (`∀M∈M …` stays mangled). For math-heavy docs (PALS's Law, the relativity
  papers) add an opt-in ML backend — Docling / Marker / Nougat — behind the existing
  `extractor` provenance field.
- **Heading-threshold tuning.** `ingest/pdf_layout.heading_level` / `classify_line`
  use generic font-size ratios; they over-detect headings on some docs. Tunable and
  now regression-tested — worth calibrating per corpus.

## Web app
- **Render Markdown in the reader.** Extracted text now carries `#` headings and `-`
  lists; the reading pane shows the markers literally. Parse/style them (headings,
  lists) instead of showing raw `#`.
- **Full-document / infinite-scroll reading view** and a "solo one POS" focus mode
  (offered earlier, not yet built).

## Tests / tooling
- **Unified pytest config.** Add a `pyproject.toml` `[tool.pytest.ini_options]`
  `testpaths = ["tests"]` (the session suite) and decide whether `ingest/tests/`
  (the NLP-layer suite, may need a live DB) runs in the same command or separately.

## Cleanup follow-ups (done this pass, noted for context)
- Root standalone scripts relocated to `scripts/` (`lexeme.py` deleted — superseded by
  `ingest/lexemes.py` + `ingest/annotate_spacy.py`).
- `ingest/extract.py` no longer imports the relocated toolkit (html/epub helpers are
  inlined in the package) — verified self-contained on a clean checkout.
