---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-07-25"
title: "doc·ray — Web Interface and Agent (MCP) Manual"
applies_to: "1.43.0"
last_reviewed: "2026-07-25"
---

# doc·ray — Web Interface and Agent (MCP) Manual

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

doc·ray is a living library and knowledge base, and it is worked from two
first-class surfaces over the same corpus:

- **The web interface** — where a person uploads, reads, curates, decides, and
  operates. It is the human surface, and the place where anything durable is
  approved.
- **The MCP server** — where an AI agent retrieves, cites, reasons over, and
  (when an operator opens the gate) proposes changes to the corpus. It is the
  agent surface, and it is deliberately not a chat wrapper around a search box:
  it exposes the corpus's own structure — outlines, ordinals, character offsets,
  entity profiles, corpus shards — so an agent can prove coverage and cite every
  claim.

Neither surface is a privileged twin of the other. Both resolve to one
principal, one row-level-security boundary, and one write path (the GraphQL
API), so a capability is not "in the webapp" or "in MCP" by accident: it is
where it is because of who is meant to exercise it.

This manual documents both surfaces, the boundary between them, and the
workflows that cross it. Reference-level contracts live beside it:
[`docs/mcp-service.md`](mcp-service.md) (as-built MCP implementation),
[`docs/public/reference/mcp.md`](public/reference/mcp.md) (tool contract),
[`docs/architecture.md`](architecture.md), and
[`docs/canonicalization_pipeline.md`](canonicalization_pipeline.md).

---

## 1. The product and its surfaces

| Surface | Who works it | What it does | Writes? |
| --- | --- | --- | --- |
| **Web interface** (`webapp/`, Flask) | People | Upload, read with annotation overlays, curate metadata and collections, audit domain seeding, share documents, operate backups and health | Yes — every write is a GraphQL mutation that queues a worker job |
| **MCP server** (`src/doc_ray/entrypoints/mcp/`) | AI agents (MCP clients) | Cited retrieval, structural navigation, entity reconciliation, evidence assembly for composition; opt-in curation and reprocessing | Read-only by default; 22 write tools appear only when an operator sets `DOCRAY_MCP_ENABLE_WRITE` |
| **GraphQL API** (`src/doc_ray/entrypoints/api/`) | Applications, both surfaces above | The complete contract, including verbs neither surface exposes (ingest by upload, delete/archive, backup/restore, cancel job, sharing mutations) | Yes — the single write path and the authoritative security boundary |
| **Admin console** (`admin-console/`, React) | Operators | Backoffice: dashboards, job monitoring/cancel, audit log, backups. Its own compose project (`docray-admin`) | Yes, via the API's admin fields |

**One security boundary.** The web interface reads Postgres directly and sends
every change to the API. The MCP server holds *no* database connection at all:
each tool forwards the caller's bearer token to `/graphql`, so principal
resolution, RLS, tenant scoping, and the schema's depth/complexity limits are
inherited rather than re-implemented.

```
  Person ──▶ web interface ──reads──▶ Postgres
                    └────writes────▶ GraphQL API ──▶ Principal ──▶ RLS ──▶ Postgres
                                          ▲
  AI agent ──MCP (Streamable HTTP)──▶ MCP server ──same Bearer──┘
```

**Owner-scoped results.** Since migration 102 (D-026) a document is readable by
its owner, an explicit ACL grantee, or a tenant admin — no role grants
corpus-wide read. An agent therefore sees exactly the corpus of the principal
whose token it presents. Give an agent a token whose visibility matches the
work; an administrator token hands it the wrong corpus.

**Agents propose; people and policy decide.** Durable truth — seeded canonical
identities, deletion, publication, catalog structure — passes through explicit
review. §8 states where that boundary sits today, including the places where the
two surfaces are not yet symmetric.

---

## 2. Getting started

### 2.1 Components

| Component | Needed for | Default |
| --- | --- | --- |
| PostgreSQL (corpus DB) | every web page | `postgresql://docray:docray@localhost:5455/docray` |
| GraphQL API | all writes; every MCP call | `http://localhost:8000/graphql` |
| Worker | executing queued jobs | runs in the compose stack |
| MCP server | agent access | `http://localhost:${MCP_PORT:-8080}/mcp` (overlay service) |
| Object store (MinIO/S3) | original bytes, covers, backups | compose `minio` |

Browsing works with the database alone. Uploads, curation, and seeding actions
need the API and the worker. MCP needs the API only — it never touches Postgres
or the object store itself.

### 2.2 Configuration — web interface

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | Corpus database connection | local compose DB (port 5455) |
| `DOCRAY_API_URL` | GraphQL endpoint for write actions | `http://localhost:8000/graphql` |
| `PORT` | HTTP port when run as a module | `5001` |
| `DOCRAY_DB_ROLE` | Non-superuser role for row-level security | `docray_client` |
| `DOCRAY_PRINCIPAL_ID` | Principal stamped on each request (enables RLS) | unset = superuser passthrough (local/dev only) |
| `DOCRAY_MAX_UPLOAD_MB` | Upload size cap | `200` |
| `DOCRAY_WEBAPP_SECRET_KEY` | Session signing key | required for real deployments |
| `DOCRAY_WEBAPP_SESSION_STORE` | `postgres` for shared sessions across replicas | in-process |
| `DOCRAY_OPERATOR_HEALTH_TOKEN` | Token required for `/operator/health` | unset = route hidden |
| `DOCRAY_WEBAPP_OIDC_*` / `DOCRAY_WEBAPP_SAML_*` | Enterprise SSO | unset = local accounts only |

### 2.3 Configuration — MCP server

| Variable | Purpose | Default |
| --- | --- | --- |
| `DOCRAY_API_URL` | GraphQL endpoint every tool forwards to | `http://api:8000/graphql` in the overlay |
| `DOCRAY_API_TOKENS` | Static bearer tokens → principal and scopes (`token=uuid[:read+write]`) | unset |
| `DOCRAY_API_JWT_SECRET` / `_ISSUER` / `_AUDIENCE` | Accept access JWTs (including OAuth-issued ones) | mirrors the API |
| `DOCRAY_MCP_ENABLE_WRITE` | Register the 22 write tools | `false` — read-only |
| `DOCRAY_MCP_ALLOWED_HOSTS` | Extra `Host` values for the SDK's DNS-rebind guard | `''` |
| `DOCRAY_PUBLIC_BASE_URL` | Public base URL; its host is allow-listed automatically | `''` |
| `DOCRAY_MCP_TIMEOUT_SECONDS` | Per-request GraphQL timeout | `30` |
| `DOCRAY_MCP_RESOURCE` / `_RESOURCE_METADATA_URL` | OAuth protected-resource identifier and metadata URL advertised to connectors | `''` |
| `DOCRAY_MCP_ALLOW_ANONYMOUS` | Local dev only — accept unauthenticated calls | `false` |
| `MCP_PORT` | Published host port for the `mcp` service | `8080` |

Behind a public hostname, **`DOCRAY_PUBLIC_BASE_URL` or
`DOCRAY_MCP_ALLOWED_HOSTS` is mandatory**: the MCP SDK's DNS-rebind guard
accepts only localhost by default and answers every other `Host` with
HTTP 421 "Invalid Host header". A connector then authenticates successfully and
still fails every call.

### 2.4 Launching

Web interface as part of the compose stack (host port via `WEBAPP_PORT`, bound
to 127.0.0.1):

```bash
docker compose up -d webapp
```

For HTTPS, add the nginx front door — it serves the webapp on
`https://localhost:${NGINX_HTTPS_PORT:-443}` and 301-redirects plain HTTP
(`${NGINX_HTTP_PORT:-80}`). First start bootstraps a self-signed certificate
(browser warning expected until replaced with a real one):

```bash
docker compose up -d nginx
```

Locally against a running stack:

```bash
flask --app webapp.app run --port 5001
# or
python -m webapp.app
```

Then open <http://localhost:5001>. The sun/moon button in the header toggles the
light ("paper") and dark ("ink") themes; the choice is remembered per browser,
and an account default can be set in **Settings → Preferences**. The header's
**Help** link opens the in-app guide at `/help`; technical screens also link
directly to their relevant guide sections. Backups appears only when the
in-app backup subsystem is switched on (`DOCRAY_BACKUP_ENABLED`, off by default —
D-033); with it off the entry is hidden and `/backups` returns 404. Backups is
visually grouped apart
from the everyday reading/search/upload navigation because restore is an
operator/destructive surface.

MCP server, layered on the managed stack:

```bash
make mcp-up          # builds + starts the `mcp` service on ${MCP_PORT:-8080}
```

Or directly, against any reachable API:

```bash
uvicorn doc_ray.entrypoints.mcp.server:app --host 0.0.0.0 --port 8080
```

Terminate TLS at a reverse proxy before exposing the MCP port beyond localhost.
In the as-built deployment a Cloudflare tunnel routes `doc-ray.com/mcp →
http://mcp:8080`, and that rule must sit **above** the `doc-ray.com *` webapp
catch-all or `/mcp` is shadowed and redirects to the login page.

---

## 3. Accounts, principals, and access

### 3.1 Signing in

| Screen | Route | Purpose |
| --- | --- | --- |
| Register | `/register` | Create a local account; email verification is sent |
| Verify email | `/verify-email` | Confirm the address; `/resend-verification` re-sends |
| Log in | `/login` | Local credentials, with lockout after repeated failures (`DOCRAY_AUTH_MAX_FAILED_LOGINS`) |
| SSO | `/auth/oidc/callback`, `/auth/saml/callback` | Enterprise identity providers, when configured |
| Profile | `/profile` | The signed-in principal, its roles, and tenant |
| Settings | `/settings` | Display name (`/settings/profile`), password, email change with confirmation, and preferences |
| Log out | `/logout` (POST) | Ends the session |

**Preferences** (`/settings/preferences`) persist the theme default, the default
reader view, and rows-per-page, so the browser is not the only place a choice
can live.

### 3.2 Principals and row-level security

By default (no `DOCRAY_PRINCIPAL_ID` set) the web process connects with full
database privileges — suitable for local, single-user work only. Setting
`DOCRAY_PRINCIPAL_ID` (with `DOCRAY_DB_ROLE`, default `docray_client`) drops the
connection to a non-superuser role and stamps every request with that principal,
so the database's row-level security policies decide which documents are
visible. Authenticated web sessions resolve their own principal per request.

### 3.3 Sharing a document (`/doc/<id>/sharing`)

- **Organization visibility** — flip a document between `private` and `org`.
- **Grant access by email** — permission `read` or `annotate`. A grantee sees
  the document in their own corpus.
- **Revoke** — removes a grant at any time.

Only the owner (or a tenant admin) manages sharing. These grants are the same
ACL the API and MCP enforce, so sharing a document is also how you widen what an
agent acting as that grantee may read.

### 3.4 Credentials for agents

Two ways an MCP client authenticates, both resolving to a doc·ray principal:

1. **Static bearer token** — a `DOCRAY_API_TOKENS` entry, optionally carrying
   scopes: `token=<principal-uuid>:read+write`. Simple, appropriate for a
   service agent.
2. **OAuth 2.1** — for connectors (Claude Desktop and similar). doc·ray runs a
   full Authorization Server: RFC 7591 Dynamic Client Registration
   (`/oauth/register`) → `/oauth/authorize` (browser login + consent, PKCE S256)
   → `/oauth/token`, advertised through
   `/.well-known/oauth-authorization-server` and
   `/.well-known/oauth-protected-resource/mcp`. No pre-shared secret is needed.

The **consent screen** names the connecting client, the signed-in subject, the
redirect target, and the scopes that will *actually* be granted — bounded by the
consenting user's RBAC (D-027), so a `reader` cannot grant `write` and an
unresolvable principal degrades to read-only. Approving a connector never grants
more than the person approving it holds.

Its wording follows the same scopes rather than assuming one of them: a
read-only grant says the application cannot change anything, and a
`read write` grant states what write actually covers — curating metadata,
entities, collections, and processing runs, and archiving or deleting documents
the user can write to (the access token reaches the GraphQL API, not only the MCP
tool surface). Consent that understates the grant is not consent, so the two are
tested together in `test_oauth_web.py`.

---

## 4. The web interface

### 4.1 Help (`/help`)

Short in-app guide for the core workflows: reading the corpus, following
pipeline status, curating domain seeding, and operating backups. Reachable from
every page through the masthead Help link, with deep links from the pipeline
status, seeding, and backups screens. It also carries a glossary — lexeme,
occurrence, annotation, canonicalization, domain seeding, POS codes.

### 4.2 Upload (`/upload`)

Drag in (or pick) a PDF, EPUB, HTML, Markdown, plain-text, DOCX, PPTX or SVG
file — optionally with an explicit
title — and submit. By default the **full pipeline** checkbox is on: the file is
ingested, then automatically annotated, semantically enriched, and canonicalized,
with no further clicks. Untick it to ingest only. Uploads are capped in size
(200 MB by default; `DOCRAY_MAX_UPLOAD_MB` overrides). Submitting lands you on
the pipeline-status page (§4.3) for the new document.

### 4.3 Pipeline status (`/job/<id>`)

Shows every background job attached to the uploaded document — ingest, annotate,
semantic-enrich, canonicalize, and anything run later — with live status pills.
The page refreshes itself every 3 seconds while anything is queued or running
and stops when the pipeline finishes, linking to the document and its
domain-seeding screen. The table uses numbered steps for scanning; the raw job
reference remains available as row metadata without dominating the page. Note
the table lists *all* of the document's jobs, so later re-runs and seeding jobs
appear here too.

### 4.4 Corpus (`/corpus`)

The daily working screen, so it opens straight onto the work. A single header row
carries the page title on the left and the corpus-wide totals (documents,
sentences, lexemes, occurrences) on the right; everything below it is the
document table — title, type, language, size, sentence count, and which
annotation tools have run — under its bulk-action toolbar. Click a title to open
the reader. If the corpus is empty, the table shows an upload call-to-action
instead of an unexplained empty grid.

**View modes.** The same documents can be laid out four ways, chosen from the
switcher above the list; the choice, along with sort, density, and grouping,
lives in the URL, so a view is shareable and bookmarkable:

| Control | Values | What it does |
|---|---|---|
| **View** | `table` · `compact` · `gallery` · `grouped` | `table` is the full detail grid (default). `compact` is one line per document — ~3–4× the documents per screen. `gallery` is a cover-forward card grid. `grouped` sections the list under headers. |
| **Sort** | `size` · `recent` · `title` · `sentences` · `pages` | Orders the list. Default `size` (largest first). |
| **Density** | `comfortable` · `compact` | Tightens row/card spacing. |
| **Group by** | `type` · `language` · `status` · `none` | In the `grouped` view, sections the documents with a per-group count and sentence subtotal. |

Any unrecognized value falls back to its default (the `sort` value is
whitelisted and never reaches SQL), so a hand-edited or stale URL is always safe.
Example: `/corpus?view=grouped&group_by=type&sort=recent&density=compact`.
Selection and the bulk-action toolbar work identically in every view.

**Bulk actions** (selection + toolbar, capped per batch): **Tag selected**,
**Archive selected**, **Re-annotate selected**, **Re-extract selected PDFs**
(non-PDFs are skipped and reported as skipped, not silently dropped). Each
action queues jobs through the API.

### 4.5 Document reader (`/doc/<id>`)

The reader shows the document paginated (40 sentences per page), as one
continuous full-document scroll, or as the **original** pages, with its lexical
annotation overlaid:

- **Header strip** — cover, title, and the metadata pills: language, type,
  character count, pages, status, plus links to **domain seeding →** (§6),
  sharing, and the original download. Kept to one compact strip so the text
  starts high.
- **Control bar** — the view switches, the POS focus control, and a page
  counter, pinned under the masthead while you scroll. Paging never requires
  scrolling back: use the counter's **prev / next**, or the **←** and **→**
  arrow keys anywhere outside a form field. Either way the new page lands on the
  first line of text rather than at the top of the header.
- **Structured text** — Markdown heading markers (`#`, `##`, `###`) and bullet
  markers (`-`) render as reader headings and lists instead of raw extraction
  syntax.
- **Annotated text** — every analyzed word is underlined in its part-of-speech
  color and also carries a compact POS sigil plus an accessible label, so the
  annotation is not color-only. Hover a word for its lemma and POS; click it to
  open that lexeme's page. Sentence ordinals appear in the margin.
- **Legend chips** — click a POS chip (NOUN, VERB, …) to mute/unmute that class
  in the overlay. Use the POS focus control to highlight exactly one selected
  part of speech while dimming the rest. The chips sit below the control bar and
  scroll away with the page: they are a reference, not a control you reach for
  mid-paragraph.
- **Annotated / Plain / Original** — `?annotate=0` drops the overlay;
  `?view=original` shows the source itself (§4.6).
- **Pages / Full document** — switch between the default paginated reader and a
  continuous `?view=all` scroll with no sentence pager.
- **Reading options** (`Aa Reading`) — three settings, all carried in the URL so
  a reading setup can be shared and survives a page turn:
  - **Layout** — `?layout=scroll` (default) is the single scrolling column.
    `?layout=spread` lays the text out as two facing pages sized to the window
    and turned rather than scrolled; the leaf counter in the bar shows where you
    are within the current run of text. Because "fits without scrolling" only
    holds if nothing else on the page is taller than the window, the spread is a
    focus mode: the stats rail, the site footer, the header's cover and metadata
    pills, and the sentence ordinals in the margin all step aside, and return in
    scroll layout. Below 700 px wide the spread becomes a single window-height
    page, turned the same way. It needs JavaScript; without it the reader stays
    a scrolling column.

    The spread also **paginates by sentences, not by source page** (60 per
    request). A PDF page routinely holds little or no extracted text — front
    matter, plates, "this page intentionally left blank" — and a page-sized
    spread of one of those is a blank screen. Switching layout maps your position
    between the two counts, so `page` means a source page in scroll layout and a
    run of sentences in the spread, and neither switch loses your place. In
    scroll layout a source page with no extracted text says so instead of
    rendering as an empty column.
  - **Size** — `?size=compact` / `comfortable` (default) / `large`.
  - **Face** — `?face=sans` (default) or `?face=serif`. Both resolve to locally
    installed families, so neither adds a webfont to a text-heavy page.
- **Rail** — POS distribution for the document and its 15 most frequent verbs,
  each linked to its lexeme page; plus the metadata, reprocessing, and
  suggestion controls of §4.6.

If a document shows no annotation, the reader states whether annotation is
queued/running or has not run yet. The extracted text remains readable while the
overlay is absent; see §7 for how the pipeline runs annotation.

### 4.6 Original view and layout overlays (`/doc/<id>?view=original`)

The **Original** view shows the document as it was published, with annotation
drawn on top of it rather than on re-flowed text:

- **PDF** — the rendered page image with absolutely-positioned overlay boxes
  over the analyzed spans. Boxes scale with the image, so the page fits the
  window height and remains legible. In spread layout two facing pages turn at
  once.
- **EPUB / DOCX / MD** — the original markup, with the same spans highlighted
  inline.
- **Overlay mode** — **Grammar** (part-of-speech) or **Entities** (named-entity
  spans). Each box links back to the lexeme page.
- When page geometry is missing, the view says so and — for PDFs — offers the
  **Re-extract** control that would produce it, rather than showing an empty
  frame.

### 4.7 Metadata, suggestions, and reprocessing (reader rail)

- **Edit metadata** — title, description, and comma-separated tags, applied
  through the API and recorded in the document's metadata history.
- **Metadata suggestions** — **Suggest** queues a verified AI suggestion job.
  Pending suggestions are listed with the field, the suggested value, the
  model's reason, a confidence band (High / Medium / Low) and its numeric
  confidence, plus status and source. Per PALS's Law the suggestion is a
  proposal under review, never an applied edit. **The web interface displays
  these suggestions and the **Review** inbox can approve or reject selected
  proposals through `decideDocumentMetadataSuggestions`. The model never applies
  a suggestion on its own.
- **Reprocessing steps**, each queued individually and each disabled while its
  own job is active, with a failed last attempt reported instead of silently
  offered again:

| Control | Does | Notes |
| --- | --- | --- |
| **Re-extract** | Rebuild text and page geometry from the original | PDF only |
| **Re-annotate** | Full annotation again (tokens, POS, named entities) | Replaces entity mentions — re-run Re-canonicalize afterwards |
| **Refresh word frequency** | Recompute lexicon/frequency data from current text | Leaves entities and POS untouched |
| **Re-enrich** | Recompute deterministic semantic enrichment | — |
| **Re-canonicalize** | Re-run deterministic entity canonicalization | Links only what the registry already knows (§6) |

### 4.8 Collections (`/collections`, `/collections/<id>`)

Named containers over the corpus, and the right tool when the intent is a group
rather than a label:

- **Manual** — membership you curate; the detail page adds and removes documents.
- **Smart** — auto-populated from a metadata rule (type / status / tags), so it
  stays current as new documents match.

A collection can be promoted to a **lens** — a named scope for entity identity
(the reserved `global` lens holds real-world, Wikidata-linkable identities;
scope lenses are work-local worlds). The Collection page promotes/demotes it and
chooses an optional parent through the guarded GraphQL path; a member Document's
reader rail sets or clears its default Lens. A Lens is currently vocabulary: it
does not yet change how entities resolve.

### 4.9 Lexemes (`/lexemes`)

The corpus vocabulary. Filter by lemma substring, part of speech, and language;
rows are ordered by how often each lexeme occurs corpus-wide (100 per page).
Click a lemma for its detail page. The table uses the lemma as the meaningful
identity instead of foregrounding the opaque lexeme key.

### 4.10 Lexeme detail (`/lexeme/<uid>`)

One dictionary entry: the lemma, its language and POS, the distinct surface
forms observed in the corpus (up to 25), and keyword-in-context snippets of its
occurrences (up to 80), each linking back to the source document.

### 4.11 Search (`/search?q=…&mode=…`)

Three explicit modes share one RLS boundary:

- **Keyword** — case-insensitive literal Sentence matches with context.
- **Meaning** — learned `semanticSearchSentences` ranking. When the API or active
  model coverage is unavailable, the page says so and falls back to Keyword.
- **Images** — cross-modal text-to-image results with actual thumbnails,
  Document/page provenance, class, and similarity. An unavailable vision stack
  renders an unknown coverage state rather than a false empty corpus.

### 4.12 Review (`/review`)

One inbox with three separately paginated lanes and persistent pending counts:

- **Metadata** — approve/reject generated metadata suggestions.
- **Entities** — inspect source context, record durable mention corrections, and
  decide seed candidates before applying approved seeds.
- **Visual** — inspect the proposed caption, source image, grounding evidence,
  and score band; approve to commit or reject to keep it out of trusted reads.

Every action crosses an authorized, audited GraphQL mutation. A lane read or
count failure renders an explicit error without blanking the other decisions.

### 4.13 Briefing (`/briefing`)

Builds an evidence pack from visible Documents through `briefingPack`: ranked
sources, exact Sentence passages and citations, entity frequencies, coverage,
retrieval method, gaps, and downstream guidance. The service assembles evidence
only; it does not generate narrative or write a derived claim into the corpus.

### 4.14 Backups (`/backups`)

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
object-store credentials. No MCP tool can create or restore a backup (§5.8).

### 4.15 Operator health (`/operator/health`)

Restricted page for read-only operational checks. Set
`DOCRAY_OPERATOR_HEALTH_TOKEN` on the webapp process, then request the page with
either `?token=<token>` or the `X-Operator-Token` header. When the token is unset
or wrong, the route returns 404.

The page renders corpus-health totals, document/job findings, database size,
connection pressure, lock/slow-query findings, and table-bloat findings. Issues
are grouped as `error`, `warning`, and `info`; the browser reads the same
Postgres store directly and does not write to the corpus.

Operators can also request `/operator/health.json` with the same token. That
route returns the stable `operator-health.v1` JSON contract: corpus totals,
job/status counts, issue groups, database size/connection/table summaries, and
the same corpus/database findings shown in the browser page. The same gate
protects `/operator/doc/<id>/metadata`, an operator-only metadata correction form
with the document's metadata history.

### 4.16 In-app definitions and tooltips

The browser defines core terms in place instead of assuming prior project
knowledge. Major feature pages include one-line descriptions for the work they
perform, and the Help page includes a glossary.

POS chips and status pills use the same shared tooltip affordance: hover with a
pointer, focus with a keyboard, or tap to focus on touch devices to reveal the
plain-language definition. Screen-reader-only tooltip text is also present in the
control.

### 4.17 Admin console (separate application)

Operator backoffice for running the deployment — dashboards over `/health`,
`/readyz` and `/operator/health.json`, job monitoring with cancel and re-extract,
a documents overview, the security audit log, and backup create/restore. It ships
as its own compose project (`docray-admin`) behind an nginx reverse proxy that
serves it same-origin with the API and webapp, because the API ships no CORS.
Tenant/user/role administration remains CLI-only (`auth_admin`) plus
SCIM/OIDC/SAML; see [`admin-console/README.md`](../admin-console/README.md) for
what is wired and what is an honest gap.

---

## 5. The MCP surface — agents working the corpus

### 5.1 What the server is

A stateless Streamable-HTTP MCP server at **`/mcp`** (`mcp==1.28.*`, FastMCP).
It registers **21 read tools + 3 composition-grounding tools** unconditionally,
**22 write tools** only when `DOCRAY_MCP_ENABLE_WRITE=true`, and **8 `docray://`
resources** (3 concrete + 5 URI templates). It owns no database pool: each tool
call is a GraphQL request carrying the caller's own token.

Two design decisions matter more than the tool count:

**It teaches itself.** `SERVER_INSTRUCTIONS` is advertised in the `initialize`
response, so a client sees the retrieval model before its first call: doc·ray is
retrieval, not reading; every result carries a citation handle; the cheap path is
`list_documents` → `read_document_outline` → ranged `read_document_sentences` →
rare-term `search_sentences`. The failure mode this fixes was never a missing
capability — it was an agent re-deriving the model mid-task, searching with
common words, reading a flooded result as *missing content*, and reaching for the
ranged reader only after its budget was spent. The lesson is paid once, in the
server, instead of in every session.

**It reports its own limits.** Capped results say they are capped, heuristic
structure says it is heuristic, and reconciled entity types carry the measured
disagreement. An agent that cannot see the edge of a result set will confidently
describe a page as a total.

### 5.2 Connecting

```json
{
  "mcpServers": {
    "doc-ray": {
      "url": "https://doc-ray.example/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Or let an OAuth-capable connector register itself (§3.4) — Dynamic Client
Registration + PKCE, with the consent screen bounding scopes to the approving
user's RBAC.

Confirm the connection and the scope with one call: `list_documents limit=5`
returns the documents that token's principal may read, newest first. An empty
list means the principal owns nothing visible — owner-scoping working as
intended, not a broken connection.

### 5.3 The retrieval contract

Five guarantees an agent can build on, and one trap each removes:

| Mechanism | Guarantee | What it prevents |
| --- | --- | --- |
| **Citation handles** | Every sentence carries `ordinal`, `charStart`, `charEnd` — in every response format | An excerpt that cannot be traced back to its source |
| **`coverage` on `search_sentences`** | `hit_count`, `ordinal_min`/`ordinal_max`, `limit`, `limit_reached`, plus a `note` in words | Reading "200 hits, all below ordinal 400" as *little about the ending* rather than *too much about the beginning* |
| **`source` on `read_document_outline`** | `headings` (real structure) · `uniform` (even grid — aim reads, but the titles are **not** chapter names) · `empty` | Quoting a fallback grid's opening sentences as chapter titles |
| **`typeConsistency` on `entity_profiles`** | Arithmetic: the dominant type's share of an entity's mentions. 1.0 = the annotator never contradicted itself; 0.36 = it constantly did | Trusting an NER type that the corpus itself disputes |
| **Per-tool cursors** | A `search_sentences` cursor is invalid for `read_document_sentences` (use ordinals there); the mismatch returns a typed `bad_input` naming the fix | Burning calls interpreting an opaque rejection |

**Token lever.** `read_document_sentences` and `search_sentences` accept
`response_format: "detailed" | "concise"`. `concise` drops the row id, the
per-row cursor (the last row of a page keeps one — that is the only one used to
continue), and, for a document-scoped search, the redundant title and
`documentId`. Measured on a realistic 200-sentence page: 47,314 → 29,876
characters, a 37% reduction, taken on the wire as a smaller GraphQL selection
rather than fetched and discarded. What `concise` never drops is attribution — a
cheaper answer that cannot be cited is not a cheaper answer.

**Typed failures.** Errors arrive as a stable `kind` with a retriability flag and
a hint: `bad_input` (fix the arguments), `permission_denied` (fix the
token/scopes), `not_found`, `unsupported` (this deployment cannot serve the
capability — never retriable), `upstream_unavailable` (retriable),
`upstream_error`. Malformed document ids and unknown `response_format` values are
rejected locally, before spending a round-trip.

**Auditability.** Every tool and resource call emits one INFO line on the
`doc_ray.mcp.audit` logger: tool name, a non-reversible fingerprint of the
caller's token (never the token), the outcome (`ok` or the typed error kind), the
duration, and clipped argument summaries. An operator can answer "who called
what, when, and did it work" without reconstructing it from transport logs.

### 5.4 Read tools (always registered)

| Tool | Purpose |
| --- | --- |
| `list_documents(limit, doc_type?, status?, after?)` | The visible corpus, keyset-paged |
| `get_document(document_id)` | One document's metadata and counts |
| `read_document_outline(document_id, max_entries)` | Addressable ordinal ranges + `source` provenance — the map that aims the reader |
| `read_document_sentences(document_id, limit, after?, from_ordinal?, to_ordinal?, page?, response_format?)` | In-order read: sequential walk to exhaustion **or** random-access ordinal/page slice; verbatim text + offsets |
| `search_sentences(term, document_id?, limit, response_format?)` | Keyword-in-context hits with `coverage` and `note` |
| `full_text_search(query, limit)` | Whole documents ranked by stemmed relevance |
| `semantic_search_sentences(query, document_id?, limit)` | Meaning-based sentence retrieval; the antidote to lexical flooding. Typed `unsupported` where the sentence stack is absent |
| `entity_profiles(document_id?, entity_type?, limit)` | One row per entity: merged spellings, type spread, `typeConsistency`, `resolved` |
| `entity_frequency(document_id?, entity_type?, limit)` | **Raw** NER rows, one per (surface, type) — prefer `entity_profiles` |
| `entity_mention_corrections(document_id)` | The durable human NER-correction log for a document |
| `lexeme_kwic(uid, document_id?, limit)` | Concordance for one lexeme |
| `list_collections(limit, kind?, after?)` / `get_collection(collection_id, document_limit)` | Collections and their members |
| `list_lenses(limit, after?)` | The collections marked as entity-resolution scopes |
| `corpus_manifest(shard_count)` / `corpus_shard(shard_index, shard_count, limit, after?)` | Deterministic, disjoint corpus partition for a map-reduce sweep |
| `document_images(document_id, limit)` · `image_class_summary(document_id)` · `visual_captions(document_id, limit)` · `visual_review(document_id?, status, limit, after?)` · `cross_modal_image_search(query, limit)` | The visual layer: extracted page images, tier-class tallies, committed captions, untrusted proposals, and text→image search (`unsupported` where the vision stack is absent) |
| `get_job(job_id)` | Poll a job enqueued by a write tool |

### 5.5 Composition-grounding tools

Each assembles several reads into one cited evidence bundle, and each states its
own gaps rather than presenting a page as a total:

| Tool | Returns |
| --- | --- |
| `briefing_pack(topic, max_documents=6, passages_per_document=4, max_entities=25)` | A cited evidence pack on a topic across documents |
| `entity_dossier(name, max_mentions=40)` | Everything the corpus says about an entity as cited mentions, plus `document_count`, `truncated`, `gaps`, and `guidance` |
| `document_evidence(document_id, focus, max_passages=12)` | Cited passages on one focus within one document |

`entity_dossier` draws its **document roster from `full_text_search`** — the
complete set of documents containing the entity, independent of `max_mentions` —
so a document with `cited_mention_count: 0` contains the entity but had no
passage cited on this page. When the cited page or the roster hits its cap, the
`gaps` field says so and `guidance` tells the agent not to claim totals. This is
PALS's Law applied at the read surface: the tool refuses to let a capped sample
be mistaken for the record.

### 5.6 Write tools (opt-in — `DOCRAY_MCP_ENABLE_WRITE=true`)

Registered only when writes are enabled, so a read-only deployment does not even
list them in `tools/list`. Enabling the gate is **necessary but not sufficient**:
each call forwards the caller's token into the API, whose guards remain
authoritative — an authenticated principal with the `write` scope and billing
entitlement (audited), plus a per-document ACL grant for document-scoped writes.
A caller lacking either gets a typed `permission_denied`. Every tool here
enqueues a **reversible** job or records a curation decision.

| Group | Tools |
| --- | --- |
| **Reprocessing** (returns `Job` — poll with `get_job`) | `reannotate`, `reextract_document`, `annotate_document`, `semantic_enrich_document`, `embed_document_sentences`, `canonicalize_document(resolve=False)` |
| **Cataloging** | `update_document_metadata(title?, description?, tags?, authors?)`, `bulk_tag_documents`, `suggest_document_metadata`, `decide_document_metadata_suggestions(approve_ids?, reject_ids?)` |
| **Entity verification** | `correct_entity_mention(action, char_start, char_end, …)`, `suggest_seeds`, `decide_seeds`, `seed_approved` |
| **Visual verification** | `decide_visual_captions(approve_ids?, reject_ids?)` — commits only explicitly approved proposals |
| **Collections and lenses** (synchronous; return the container) | `create_collection`, `update_collection`, `delete_collection`, `add_document_to_collection`, `remove_document_from_collection`, `promote_collection_to_lens`, `demote_lens`, `set_document_default_lens` |

`correct_entity_mention` is the agent-facing half of the NER verification layer.
Entity rows are rebuilt by every annotation run, so a correction is recorded
against the **stable span identity** (`document_id` + character offsets) and
re-applied on every later pass, instead of being written into a derived table the
next run would erase. `original_type` and `rationale` are stored, so an override
is attributable and reversible. Actions: `reclassify` (wrong type), `reject` (not
an entity), `add` (a missed entity), `adjust_span` (right entity, wrong
boundaries). Read `entity_mention_corrections` first so a fix is not recorded
twice.

`delete_collection` removes **only the container** — member documents are never
touched. Promoting a collection to a lens is a curation decision: mark a
collection a scope only when its documents share an identity world. The database
enforces the rules — a default lens must be a scope the document belongs to, a
parent lens must itself be a scope, the parent graph may not cycle, and `global`
cannot be renamed, demoted, or deleted.

### 5.7 Resources — `docray://` URIs

Alongside tools, the server declares MCP's **resources** primitive: addressable,
host-cacheable handles a client enumerates and fetches with `resources/read`.
They are **addressing, not capability** — each read goes through the same read
tools under the same token and RLS, and the bytes still land in the caller's
context.

| URI / template | Serves |
|---|---|
| `docray://corpus/documents` | **concrete** — the document index; start here |
| `docray://corpus/collections` | **concrete** — the collection index |
| `docray://corpus/manifest` | **concrete** — shard map at the default width (8) |
| `docray://doc/{document_id}` | Full text, one sentence per line; truncates **explicitly** at 200 000 chars and names the ranged template for the remainder |
| `docray://doc/{document_id}/sentences/{start}-{end}` | The bounded, citable ordinal slice |
| `docray://doc/{document_id}/pages/{page}` | Every sentence on one source page |
| `docray://collection/{collection_id}` | One collection + its members |
| `docray://corpus/shards/{shard_count}` | The corpus shard map |

The three concrete entry points exist because `resources/list` and
`resources/templates/list` read disjoint registries: a template-only surface
makes `resources/list` return `[]`, which clients render as "this server has no
resources". Hosts must still surface `resources/*` to the model; clients that
expose only tools keep working unchanged.

### 5.8 Deliberate absences

Not exposed over MCP, at any gate setting:

- **Ingest** (`ingestDocument`) — a multipart upload, not a plain mutation.
- **Destructive and admin verbs** — delete, archive, backup, restore, cancel job.
- **Sharing mutations** — `shareDocument` / `revokeDocumentShare`.

These remain on the GraphQL API and in the human surfaces. The line is not
technical timidity: an agent may propose and reprocess, but the acts that destroy
data, change who can see it, or publish it stay with a person or an explicit
policy.

### 5.9 Worked agent workflows

**Exhaustive coverage of one document** — outline first, then ranged reads; never
grind keyword searches for coverage.

```jsonc
{"name": "read_document_outline", "arguments": {"document_id": "1b7dcdd2-…", "max_entries": 8}}
// → {"source": "headings", "totalSentences": 10063, "entries": [ … {"title": "58", "ordinalStart": 8496, "ordinalEnd": 10062} ]}

{"name": "read_document_sentences",
 "arguments": {"document_id": "1b7dcdd2-…", "from_ordinal": 8496, "to_ordinal": 10062,
               "response_format": "concise"}}
```

For 100% coverage, page sequentially instead: call with `after: null`, feed the
last row's cursor back as `after`, stop when a page returns `[]`.

**Sweeping the whole corpus with N workers** — and *proving* the sweep was
complete, because the per-shard counts sum to the total.

```jsonc
{"name": "corpus_manifest", "arguments": {"shard_count": 8}}
// → {"shardCount": 8, "totalDocuments": 412, "shards": [{"index": 0, "documentCount": 51}, …]}
{"name": "corpus_shard", "arguments": {"shard_index": 3, "shard_count": 8, "limit": 100}}
// → page to exhaustion via `after`; Σ shard counts == totalDocuments, no id in two shards
```

A shard is the intersection of `hash mod N` and the caller's RLS scope, so "the
whole corpus" always means "the whole corpus this token may read."

**Who and what a document is about** — `entity_profiles`, and read
`typeConsistency` before quoting a type. `resolved: false` means the row is
reconciled NER and nothing more: the entity is not backed by the canonical
registry yet (§6).

**A cited briefing** — `/briefing`, `briefing_pack`, or `entity_dossier`, then honor `gaps` and
`guidance`: state what the cited passages support, and say the sample was capped
when it was.

**Correcting a mis-typed entity** — read the existing corrections, record the
fix, poll the job.

```jsonc
{"name": "entity_mention_corrections", "arguments": {"document_id": "f0b3d10c-…"}}   // → []
{"name": "correct_entity_mention", "arguments": {
   "document_id": "f0b3d10c-…", "action": "reclassify", "char_start": 16, "char_end": 22,
   "entity_type": "GPE", "original_type": "PERSON", "rationale": "London is a place, not a person"}}
// → {"id": "01J…", "kind": "apply_corrections", "status": "queued"}
{"name": "get_job", "arguments": {"job_id": "01J…"}}
// → {"status": "succeeded", "result": {"corrections_applied": true}}
```

**Curating structure** — `create_collection` → `add_document_to_collection` per
document → `get_collection` to read it back; `promote_collection_to_lens` only
when the set genuinely shares an identity world. The person auditing the result
sees it in the web interface at `/collections`.

---

## 6. The domain-seeding workflow

Seeding teaches the registry a document's domain vocabulary so the
canonicalization pipeline can link mentions deterministically. The screen
(`/doc/<id>/seeding`) shows the required order up front: mine candidates → audit
identities → approve exact matches → seed approved rows. Automating it end-to-end
is deliberately **not** supported: knowledge-base resolution sometimes lands on a
plausible-but-wrong identity, and one wrong seed pollutes the shared registry for
every document. The screen exists to make the human audit fast instead of
skippable. An agent may drive the same steps over MCP (`suggest_seeds` →
`decide_seeds` → `seed_approved`) with a write-scoped token — the judgment being
delegated is exactly the judgment described below, so delegate it knowing that.

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

The description is where wrong identities announce themselves. Real examples from
this corpus:

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
Decisions are reversible while a row is pending, approved, or rejected — you can
re-decide until you seed.

### Step 4 — Seed

**Seed N approved** asks for explicit confirmation, then queues a job that writes
the approved entities (and their multilingual aliases) into the canonical
registry and marks those rows **seeded**. Seeded is final: those rows record what
entered the registry and can no longer be re-decided.

After seeding, re-run canonicalization for the document (the reader's
**Re-canonicalize** control, the API's `canonicalizeDocument` mutation, or MCP's
`canonicalize_document`) — newly seeded vocabulary links immediately, with no
model calls. Fresh uploads need no such step: the default upload pipeline
canonicalizes automatically once annotation lands.

### Status lifecycle

```
pending ──approve──▶ approved ──seed──▶ seeded   (final)
   │  ▲                 │
   └──┴───reject────────┘        (pending / approved / rejected are re-decidable)
```

---

## 7. Background jobs and freshness

Mine and seed actions return immediately with a notice like
*"mining job 01KWH… queued — refresh shortly"*. The work happens in the worker;
**refresh the page** to see the outcome. Agents poll instead: every
job-returning MCP write tool hands back a `Job`, and `get_job` reports `status`,
`progressStage`, `progressCurrent`/`progressTotal`, `result`, and `error`. The
same information is on the API: `{ job(id: "…") { status result error } }`.

Document analysis follows the same model: a freshly uploaded document appears in
the corpus as soon as ingestion finishes, but annotation — and therefore the
reader overlay, entity results, mention mining, and language-aware search —
arrives when its annotation job completes. A reader page or a tool result that
looks thin on a new document is usually a pipeline that has not finished, not
missing content.

---

## 8. Where the surfaces meet — the governance boundary

The two surfaces are intentionally asymmetric. Recording where, honestly, is
cheaper than discovering it mid-task:

| Capability | Web interface | MCP | GraphQL API |
| --- | --- | --- | --- |
| Ingest by file upload | Yes | No | Yes |
| Read with annotation overlays | Yes | Text + offsets, no rendering | Yes |
| Keyword / semantic / image search | Yes | Yes | Yes |
| Corpus sharding for exhaustive sweeps | No | Yes | Yes |
| Domain-seeding decisions | Yes | Yes (write gate) | Yes |
| Metadata suggestions: request | Yes | Yes (write gate) | Yes |
| Metadata suggestions: approve/reject | Yes (Review) | Yes (write gate) | Yes |
| Entity-mention corrections | Yes (Review) | Yes (write gate) | Yes |
| Collections: create/edit | Yes | Yes (write gate) | Yes |
| Lens promotion / default lens | Yes | Yes (write gate) | Yes |
| Visual-caption review | Yes (Review) | Yes (write gate) | Yes |
| Cited briefing pack | Yes | Yes | Yes |
| Sharing and visibility | Yes | No | Yes |
| Archive / delete / backup / restore / cancel job | Yes (operator surfaces) | **No, by design** | Yes |

The principle the table follows is constant: **AI proposes, verification
decides.** Suggestions arrive as
suggestions with a confidence and a reason. Corrections are attributable and
reversible. Seeded identities are final and therefore human-gated. Destruction,
publication, and access changes never reach an agent's tool list at all.

---

## 9. Troubleshooting

### Web interface

| Symptom | Likely cause / fix |
| --- | --- |
| Red error banner on the seeding screen | The GraphQL API is unreachable or rejected the action. Check `DOCRAY_API_URL` and that the API container is up. Reads still work without the API. |
| "Mine candidates" finishes but the table stays empty | The document has no annotation yet (mining reads mentions and lexemes). Annotate first, then mine. |
| Seeding buttons queue jobs but nothing happens | The worker is not running — jobs wait in the queue until it is. |
| Reader shows plain text, no colored words | The document is ingested but not annotated. |
| Original view says the overlay is unavailable | Page geometry is missing. For PDFs, run the offered **Re-extract**. |
| Search misses inflected forms | `/search` is a literal substring match by design; use `fullTextSearch` (API/MCP) for stemmed search. |
| Page not found for a document link | The document was deleted (deletion cascades all derived data). |
| A reprocess button is disabled | That step already has a queued/running job; a permanently failed last attempt shows as **Retry**. |

### MCP

| Symptom | Likely cause / fix |
| --- | --- |
| Client authenticates, then every call fails with HTTP 421 | The SDK's DNS-rebind guard rejected the `Host`. Set `DOCRAY_PUBLIC_BASE_URL` and/or `DOCRAY_MCP_ALLOWED_HOSTS` on the `mcp` service. |
| `/mcp` redirects to a login page | A catch-all route is shadowing it. In the Cloudflare tunnel, the `/mcp → http://mcp:8080` rule must sit above the webapp catch-all. |
| Write tools missing from `tools/list` | `DOCRAY_MCP_ENABLE_WRITE` is unset — the surface is read-only by design. |
| Write tool returns `permission_denied` | The token lacks the `write` scope, the principal's RBAC does not carry it, or there is no write grant on that document. |
| Tool returns `unsupported` | This deployment does not run that stack (sentence embeddings, vision). Never retriable. |
| The agent "sees no documents" | Owner-scoping: the token's principal owns nothing visible. Share the documents, or use a token for the right principal. |
| Search looks sparse | Read `coverage`. `limit_reached: true` with a narrow `ordinal_min`/`ordinal_max` means the term flooded one region — narrow the term or read the range by ordinal. |
| A cursor is rejected | Cursors are per-tool. Use ordinals with `read_document_sentences`. |
| Dynamic Client Registration returns 500 | Migration `082_oauth_clients.sql` was not applied. Apply migrations, then retry. |

---

## Related documents

- [`README.md`](../README.md) — what the project is, and the command reference.
- [`PURPOSE.md`](../PURPOSE.md) — why it exists, and the Today/Becoming boundary.
- [`docs/mcp-service.md`](mcp-service.md) — as-built MCP implementation, module
  layout, and deployment gotchas.
- [`docs/public/reference/mcp.md`](public/reference/mcp.md) — the published
  tool-level contract.
- [`docs/canonicalization_pipeline.md`](canonicalization_pipeline.md) — how
  seeded identities become links.
- [`docs/architecture.md`](architecture.md) — hexagonal internals.
- [`docs/TUTORIAL.md`](TUTORIAL.md) — a fresh install, end to end.
