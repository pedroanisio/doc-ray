# scripts/ — standalone text tools

Self-contained command-line tools, **independent of the production `ingest/`
pipeline and the database**. They read files directly and print to stdout/JSON;
nothing in `ingest/` or `webapp/` imports them. They live here (not the repo
root) to keep the root clean.

Run them from the repo root, e.g. `python scripts/extract_phrases.py file.pdf`.

| Script | What it does |
|---|---|
| `extract_phrases.py` | Read a document (txt/md/pdf/docx/epub/html), reflow lines wrapped mid-sentence, split into sentences one-per-line (+ optional stats, `--unique`, `--paragraphs`). |
| `docmodel.py` | **Lossless** hierarchical decomposition `document → section → heading/paragraph → sentence → phrase → word` with `compose(decompose(t)) == t`; JSON serialize + `--compose` to reconstruct. |
| `text_decomposer.py` | Self-contained `paragraphs → sentences → phrases → words` decomposer (rule-based, optional NLTK for grammatical phrase chunking). |
| `roundtrip.py` | Fidelity check: ingest a PDF → export → re-extract fresh and compare bytes / full_text / sentences. Uses the `ingest` package (read-only). |
| `showcase.sql` | Illustrative queries against the corpus. |

## Relationship to the pipeline

The production path is `ingest/` (extract → segment → annotate → Postgres) and
`webapp/` (browse the DB). These `scripts/` are earlier/auxiliary toolkit code
kept for standalone use; some concerns (sentence segmentation, lexeme identity,
PDF extraction) were reimplemented properly inside `ingest/` and are the
authoritative versions. The toolkit's own tests live in the top-level `tests/`
suite (`test_sentences`, `test_readers`, `test_cli`, `test_stats`, `test_docmodel`).
