---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-02"
---

# doc·ray Browser — User Manual

The doc·ray browser is the web front end of the doc·ray corpus store. It lets you
read ingested documents with their linguistic annotation overlaid, explore the
corpus vocabulary, search sentences, and curate **domain seeding** — the human
audit step that decides which knowledge-base entities enter the shared canonical
registry.

Reads go straight to the corpus database. Every action that *changes* anything
(uploading a document, mining seed candidates, approving them, seeding, and
backing up or restoring the corpus) is sent to the GraphQL API, which queues a
background job for the worker — the browser itself never writes to the store.

---

## 1. Getting started

### Prerequisites

| Component | Needed for | Default |
| --- | --- | --- |
| PostgreSQL (corpus DB) | every page | `postgresql://docray:docray@localhost:5455/docray` |
| GraphQL API | uploads and seeding actions | `http://localhost:8000/graphql` |
| Worker | executing the queued jobs | runs in the compose stack |

Browsing works with the database alone; the upload and seeding screens' actions
need the API (with its object-store configuration) and the worker up as well.

### Configuration (environment variables)

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | Corpus database connection | local compose DB (port 5455) |
| `DOCRAY_API_URL` | GraphQL endpoint for write actions | `http://localhost:8000/graphql` |
| `PORT` | HTTP port when run as a module | `5001` |
| `DOCRAY_DB_ROLE` | Non-superuser role for row-level security | `docray_client` |
| `DOCRAY_PRINCIPAL_ID` | Principal stamped on each request (enables RLS) | unset = superuser passthrough (local/dev only) |
| `DOCRAY_OPERATOR_HEALTH_TOKEN` | Token required for `/operator/health` | unset = route hidden |

### Launching

```bash
flask --app webapp.app run --port 5001
# or
python -m webapp.app
```

Then open <http://localhost:5001>. The sun/moon button in the header toggles the
light ("paper") and dark ("ink") themes; the choice is remembered per browser.
The header's **Help** link opens the in-app guide at `/help`; technical screens
also link directly to their relevant guide sections.

---

## 2. Screens

### Help (`/help`)

Short in-app guide for the core browser workflows: reading the corpus, following
pipeline status, curating domain seeding, and operating backups. The guide is
reachable from every page through the masthead Help link. Pipeline status,
domain seeding, and backups also link to their corresponding guide sections.

### 2.0 Upload (`/upload`)

Drag in (or pick) an EPUB, PDF, TXT, MD, or DOCX — optionally with an explicit
title — and submit. By default the **full pipeline** checkbox is on: the file
is ingested, then automatically annotated and canonicalized, with no further
clicks. Untick it to ingest only. Uploads are capped in size (200 MB by
default; `DOCRAY_MAX_UPLOAD_MB` overrides). Submitting lands you on the
pipeline-status page (§2.1) for the new document.

### 2.1 Pipeline status (`/job/<id>`)

Shows every background job attached to the uploaded document — ingest,
annotate, canonicalize, and anything run later — with live status pills. The
page refreshes itself every 3 seconds while anything is queued or running and
stops when the pipeline finishes, linking to the document and its
domain-seeding screen. Note the table lists *all* of the document's jobs, so
later re-runs and seeding jobs appear here too.

### 2.2 Documents (`/`)

The landing page states the current document count, lists every document in the
corpus — title, type, language, size, sentence count, and which annotation tools
have run — plus corpus-wide totals (documents, sentences, lexemes,
occurrences). Click a title to open the reader. If the corpus is empty, the page
shows an upload call-to-action instead of an unexplained empty table.

### 2.3 Document reader (`/doc/<id>`)

The reader shows the document 40 sentences per page with its lexical annotation
overlaid:

- **Header pills** — language, type, character count, pages, status, and the
  **domain seeding →** link to the curation screen (§3).
- **Annotated text** — every analyzed word is underlined in its part-of-speech
  color and also carries a compact POS sigil plus an accessible label, so the
  annotation is not color-only. Hover a word for its lemma and POS; click it to
  open that lexeme's page. Sentence ordinals appear in the margin.
- **Legend chips** — click a POS chip (NOUN, VERB, …) to mute/unmute that class
  in the overlay, isolating what you care about.
- **Plain mode** — the annotate/plain toggle (`?annotate=0`) renders the text
  without the overlay.
- **Rail** — POS distribution for the document and its 15 most frequent verbs,
  each linked to its lexeme page.

If a document shows no annotation, it has been ingested but not yet annotated —
see §4.

### 2.4 Domain seeding (`/doc/<id>/seeding`)

The curation screen for growing the canonical registry from this document.
Covered in full in §3.

### 2.5 Lexemes (`/lexemes`)

The corpus vocabulary. Filter by lemma substring, part of speech, and language;
rows are ordered by how often each lexeme occurs corpus-wide (100 per page).
Click a lemma for its detail page.

### 2.6 Lexeme detail (`/lexeme/<uid>`)

One dictionary entry: the lemma, its language and POS, the distinct surface
forms observed in the corpus (up to 25), and keyword-in-context snippets of its
occurrences (up to 80), each linking back to the source document.

### 2.7 Search (`/search?q=…`)

Case-insensitive **substring** search over sentences, first 100 hits, grouped by
document. The browser states this beside the search box and repeats the caveat
in the zero-result state. This is a literal match — it will not find inflected
variants of your query. Stemmed full-text search is available on the GraphQL API
(`fullTextSearch`), not in the browser yet.

### 2.8 Backups (`/backups`)

Capture the entire corpus — every database table plus every stored blob — as one
portable archive, or restore a previous one. The page lists catalogued archives
with their status, row/blob/document counts, and size.

- **Create backup** — optionally note why (e.g. *before re-ingest*), then submit.
  A background job builds the archive and uploads it to the object store; follow
  the job for progress. The row shows **available** once the archive is written.
- **Download** — stream an available archive (`.tar.gz`) to your machine.
- **Restore** — rewind the whole corpus to that archive. This is **destructive**
  (a confirmation guards the button): every corpus table and blob is overwritten
  with the snapshot's contents. The live job queue and the backup catalogue
  itself are left untouched, and the archive must match the database's schema
  version.

Like every other write, create and restore go through the API and run in the
worker; downloads are proxied through the API too, so the browser never needs
object-store credentials.

### 2.9 Operator health (`/operator/health`)

Restricted page for read-only operational checks. Set
`DOCRAY_OPERATOR_HEALTH_TOKEN` on the webapp process, then request the page with
either `?token=<token>` or the `X-Operator-Token` header. When the token is unset
or wrong, the route returns 404.

The page renders corpus-health totals, document/job findings, database size,
connection pressure, lock/slow-query findings, and table-bloat findings. Issues
are grouped as `error`, `warning`, and `info`; the browser reads the same
Postgres store directly and does not write to the corpus.

---

## 3. The domain-seeding workflow

Seeding teaches the registry a document's domain vocabulary so the
canonicalization pipeline can link mentions deterministically. Automating it
end-to-end is deliberately **not** supported: knowledge-base resolution
sometimes lands on a plausible-but-wrong identity, and one wrong seed pollutes
the shared registry for every document. The screen exists to make the human
audit fast instead of skippable.

### Step 1 — Mine candidates

Click **Mine candidates**. A background job scans the document for terms the
registry does not know yet, from two sources:

- `mention` — names found by entity recognition that are not yet linked;
- `lexeme` — frequent nouns (the technical-vocabulary channel).

Each candidate is resolved to a Wikidata identity **through its Wikipedia
article title** — an exact-identity method; no fuzzy search. The job takes a
little while (it paces its requests politely); refresh the page to see results.

### Step 2 — Audit each row

This is the step that matters. For every suggestion you see:

| Column | What to check |
| --- | --- |
| **term / n / src** | What the document actually says, and how often. |
| **identity** | The resolved label, the QID (linked to Wikidata), and the KB **description** — read it. |
| **type** | The mapped entity type, when known. |

The description is where wrong identities announce themselves. Real examples
from this corpus:

- *foundry* → "factory that produces metal castings" — a real entity, but the
  **wrong sense** for a typography book (type foundry ≠ metal foundry). Reject.
- Generic nouns (*time*, *book*, *year*) resolve to perfectly real entities that
  make **terrible seeds** — their aliases would attach to everything. Reject.
- **no article match** — the resolver found nothing usable. This includes
  ambiguous names (e.g. bare surnames) that lead to disambiguation pages: the
  resolver refuses to guess rather than pick a wrong identity. These rows stay
  visible so you can spot terms that deserve a manual look, but they cannot be
  approved without an identity.

When in doubt, open the QID link and look at the Wikidata item itself.

### Step 3 — Decide

Tick the checkboxes and click **Approve selected** or **Reject selected**.
Decisions are reversible while a row is pending, approved, or rejected — you
can re-decide until you seed.

### Step 4 — Seed

**Seed N approved** queues a job that writes the approved entities (and their
multilingual aliases) into the canonical registry, then marks those rows
**seeded**. Seeded is final: those rows record what entered the registry and
can no longer be re-decided.

After seeding, re-run canonicalization for the document (the API's
`canonicalizeDocument` mutation) — newly seeded vocabulary links immediately,
with no model calls. Fresh uploads need no such step: the default upload
pipeline canonicalizes automatically once annotation lands.

### Status lifecycle

```
pending ──approve──▶ approved ──seed──▶ seeded   (final)
   │  ▲                 │
   └──┴───reject────────┘        (pending / approved / rejected are re-decidable)
```

---

## 4. Background jobs and freshness

Mine and seed actions return immediately with a notice like
*"mining job 01KWH… queued — refresh shortly"*. The work happens in the worker;
**refresh the page** to see the outcome. If a job seems stuck, its status can be
checked on the GraphQL API: `{ job(id: "…") { status result error } }`.

Document analysis follows the same model: a freshly uploaded document appears
in the browser as soon as ingestion finishes, but annotation (and therefore the
reader overlay, mention mining, and language-aware search) arrives when its
annotation job completes.

---

## 5. Access control

By default (no `DOCRAY_PRINCIPAL_ID` set) the browser connects with full
database privileges — suitable for local, single-user work only. Setting
`DOCRAY_PRINCIPAL_ID` (with `DOCRAY_DB_ROLE`, default `docray_client`) drops the
connection to a non-superuser role and stamps every request with that
principal, so the database's row-level security policies decide which documents
are visible.

---

## 6. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| Red error banner on the seeding screen | The GraphQL API is unreachable or rejected the action. Check `DOCRAY_API_URL` and that the API container is up. Reads still work without the API. |
| "Mine candidates" finishes but the table stays empty | The document has no annotation yet (mining reads mentions and lexemes). Annotate first, then mine. |
| Seeding buttons queue jobs but nothing happens | The worker is not running — jobs wait in the queue until it is. |
| Reader shows plain text, no colored words | The document is ingested but not annotated. |
| Search misses inflected forms | `/search` is a literal substring match by design; use the API's `fullTextSearch` for stemmed search. |
| Page not found for a document link | The document was deleted (deletion cascades all derived data). |
