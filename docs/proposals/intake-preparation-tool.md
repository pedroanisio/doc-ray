---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-07-29"
status: proposed
---

# Intake Preparation: filename normalization, quality gating, and metadata enrichment

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

---

## 1. Summary

A pre-ingestion tool — `docray-prep` — that walks a staging directory and, for
every candidate file, produces one **intake manifest** record containing:

1. a **proposed normalized filename**, derived deterministically from verified inputs;
2. a **quality verdict** (`ok` / `degraded` / `reject`) with coded, actionable reasons;
3. **enriched bibliographic metadata** resolved from at least three public
   providers, every field carrying its provider, endpoint, retrieval time, and a
   corroboration status.

The tool does not rename anything by default and never writes to the corpus.
Renaming is a separate, explicit, reversible `apply` step. Enriched metadata
enters doc-ray through the existing suggestion path, as proposals a human
accepts — not as asserted facts.

**Complexity: L.** Five deliverable slices, each independently useful (§14).

---

## 2. What this is for, and what it is not for

The requested capability is easy to describe as *output*: files get renamed, a
report gets written, some JSON arrives from the internet. The **outcome** worth
building for is narrower and should be stated first, because it decides several
design questions later:

> An operator can point the tool at a directory of arbitrarily-named documents
> and, without opening any of them, know which ones are ingestible, which are
> already in the corpus, which are damaged, and what each one actually is.

Two things follow from that outcome statement:

- The valuable artifact is the **manifest**, not the rename. Renaming is a
  convenience on top of a judgement the manifest already recorded.
- "What each one actually is" is a **claim about the world**, and therefore
  subject to the project's verification stance. A provider's answer is evidence,
  not truth.

**Not in scope.** Content-based classification (that is `genre_classification`),
duplicate *near*-matching beyond exact content hash, OCR, repair of damaged
files, or any write to `documents` outside the existing suggestion path.

---

## 3. Untangling the request: three problems, not one

The three asks are coupled through a shared file walk, but they have different
failure modes, different trust levels, and different reversibility. Treating
them as one unit produces a tool whose worst component sets the risk profile of
the whole.

| Concern | Network? | Determinism | Reversible? | Trust level of its output |
|---|---|---|---|---|
| Filename normalization | No | Total — pure function of inputs | Only if journalled | High: mechanical transform |
| Quality gate | No | Total for a fixed file | N/A (read-only) | High: measured properties |
| Metadata enrichment | **Yes** | Only with a response cache | N/A (read-only) | **Low: third-party assertion** |

The coupling that matters is this: **enrichment output feeds the filename**. A
name built from an unverified provider guess launders that guess into a
filesystem artifact, and doc-ray's existing
`_title_from_filename()` ([document_metadata_suggest.py:92](../../src/doc_ray/application/document_metadata_suggest.py#L92))
would then re-derive a title from it and offer it as a suggestion — a fabricated
fact having crossed two layers without ever acquiring a source.

**Rule N-0 (§8) closes that path:** a proposed filename may only use metadata
fields whose corroboration status is `corroborated` or `identifier-exact`.
Anything weaker falls back to the normalized original stem.

---

## 4. Architectural significance

This is not a script. Assessed against the five factors that make a decision
architecturally significant — structure, non-functional characteristics,
dependencies, interfaces, construction techniques — it touches four:

- **Dependencies.** It introduces a *new dependency class*: third-party network
  APIs on the intake path, with their own availability, rate limits, key
  management, and terms of use. doc-ray has exactly one precedent
  ([document_source_arxiv.py](../../src/doc_ray/adapters/document_source_arxiv.py))
  and it is deliberately narrow.
- **Interfaces.** The manifest becomes a durable contract between a
  pre-ingestion stage and the ingest core, and is versioned as such.
- **Non-functional characteristics.** Determinism, provenance-completeness, and
  bounded external I/O are all newly load-bearing.
- **Construction techniques.** It adds a new entrypoint class — an offline,
  filesystem-facing CLI that runs *before* the database exists in the picture.

It does **not** change system structure: it plugs into the existing hexagonal
layering with new ports and adapters and no new architectural style.

### Architecture characteristics this must satisfy

Three qualify — each is non-domain, structurally influential, and critical to
whether the tool is usable at all:

| Characteristic | Why it qualifies | How it is enforced |
|---|---|---|
| **Determinism** | Same bytes + same provider snapshot must yield the same name and the same verdict, or the manifest cannot be reviewed, diffed, or re-run | Pure normalization functions; on-disk response cache keyed by request URL; collision breaking by content hash, never by a counter |
| **Provenance completeness** | A metadata field with no recorded source is indistinguishable from a hallucination | Manifest schema makes `provenance` a required property of every enriched field; a field without it fails schema validation |
| **Bounded external I/O** | The tool must be safe to run on 10,000 files without getting the operator's IP blocked or exhausting a quota | Per-provider token-bucket limiter, response cache, hard per-run request ceiling, no retry storms |

Availability of the providers is explicitly **not** an architecture
characteristic here: every provider is optional and every provider failure
degrades one field, never the run.

---

## 5. The load-bearing decision: manifest-first, not rename-first

Two designs were available.

**Option A — in-place renamer.** Walk the directory, rename files as it goes,
write a log. Simplest to build, immediately visible result.

**Option B — manifest-first (recommended).** Walk the directory, write a
manifest, change nothing. A separate `apply` subcommand performs renames from
the manifest and writes a reversible journal.

**The trade-off, stated plainly.** Option A is one command instead of two and
gives the operator a tidy directory in a single pass; that convenience is real
and Option B does not match it. Option B costs an extra step and a file the
operator has to read. What it buys:

- Renaming becomes reviewable *before* it is irreversible.
- The quality report survives the run as data, not console scrollback.
- Enrichment results are separable from the rename — the operator can accept the
  metadata and reject the name.
- A dry run on a large directory produces exactly the same artifact as a real
  one, so the run can be evaluated without risk.

Option B is chosen because the operation is **irreversible against the
operator's own originals** and its riskiest input is third-party data. The
convenience gap is closed by `docray-prep run --apply`, which chains both steps
in one invocation for operators who have already decided to trust the output.

**Consequence to accept:** two artifacts now exist that could disagree — the
manifest and the directory. `apply` mitigates this by refusing to run against a
manifest whose recorded content hashes no longer match the files on disk.

---

## 6. Pipeline

```
                       ┌──────────────────────────────────────────┐
  staging dir  ───────▶│ S1  Probe & quality gate      (no net)   │
  (_ingest/)           │     sniff · hash · integrity · verdict   │
                       └────────────────┬─────────────────────────┘
                                        │ reject → manifest, stop here
                       ┌────────────────▼─────────────────────────┐
                       │ S2  Identifier extraction     (no net)   │
                       │     filename · embedded · first-N pages  │
                       └────────────────┬─────────────────────────┘
                       ┌────────────────▼─────────────────────────┐
                       │ S3  Provider enrichment       (network)  │
                       │     route → fan-out → reconcile → verify │
                       └────────────────┬─────────────────────────┘
                       ┌────────────────▼─────────────────────────┐
                       │ S4  Name proposal             (no net)   │
                       │     template · slug · collision break    │
                       └────────────────┬─────────────────────────┘
                       ┌────────────────▼─────────────────────────┐
                       │ S5  Manifest emit                        │
                       └────────────────┬─────────────────────────┘
                                        │
                     apply ─────────────┼──────────▶ rename + journal
                     ingest ────────────┴──────────▶ pipeline.ingest_paths
```

Stages 1, 2, 4 are pure functions over `(bytes, config)` and are unit-tested
without any I/O. Stage 3 is the only impure stage and is tested against
`httpx.MockTransport`, following the pattern already established by
[document_source_arxiv.py](../../src/doc_ray/adapters/document_source_arxiv.py).

---

## 7. Stage 1 — Quality gate

Every check is a measured property of the bytes. No check is a heuristic about
"good" content.

### Universal checks

| Check | Verdict on failure | Code |
|---|---|---|
| Extension is in the extractor allowlist (`extraction.is_supported_extension`) | `reject` | `EXT_UNSUPPORTED` |
| Magic bytes match the declared extension | `reject` | `MAGIC_MISMATCH` |
| Size > 0 and ≤ configured maximum | `reject` | `SIZE_ZERO` / `SIZE_EXCEEDS` |
| Content SHA-256 not already present in the corpus (optional, needs DB) | `degraded` | `ALREADY_INGESTED` |
| Path is a regular file, not a symlink escaping the root | `reject` | `PATH_UNSAFE` |

Reusing `extraction.is_supported_extension()` is deliberate: the gate must be
the *same* allowlist the worker enforces, or the tool would certify files that
ingest later refuses. The allowlist is defined once, in
[domain/document_formats.py](../../src/doc_ray/domain/document_formats.py):
`.pdf .txt .text .md .markdown .html .htm .epub .svg .pptx .docx` — eleven
extensions, eight format families. The tool must name it from there rather than
carry a copy (see BL-193, §20).

The corpus-presence check uses the same SHA-256 the ingest core uses for
deduplication, so the answer it gives is the answer ingest will give. It is
opt-in (`--check-corpus`) because it is the only part of the tool that needs a
database connection.

### Format-specific checks

| Format | Check | Verdict | Code |
|---|---|---|---|
| PDF | Parses; page count readable | `reject` | `PDF_UNPARSEABLE` |
| PDF | Not encrypted / not password-protected | `reject` | `PDF_ENCRYPTED` |
| PDF | Extractable-text coverage — fraction of pages yielding ≥ N characters | `degraded` | `PDF_LOW_TEXT_COVERAGE` |
| EPUB | Valid zip; `META-INF/container.xml` present and resolvable to an OPF | `reject` | `EPUB_CONTAINER_INVALID` |
| DOCX / PPTX | Valid zip; required OOXML parts present | `reject` | `OOXML_PARTS_MISSING` |
| Zip-based | Entry count, per-entry and total uncompressed size within bounds | `reject` | `ARCHIVE_BOUNDS_EXCEEDED` |
| TXT / MD | Decodes as UTF-8, or detected encoding is recorded | `degraded` | `ENCODING_NON_UTF8` |
| HTML / SVG | Well-formed enough for the existing extractor to parse | `degraded` | `MARKUP_MALFORMED` |
| Any | Extraction produces < N characters of text | `degraded` | `TEXT_YIELD_LOW` |

`PDF_LOW_TEXT_COVERAGE` is the check that earns the tool its keep: it separates
born-digital PDFs from scans before a scan enters the corpus and produces an
empty sentence table. It reports a measured fraction; the threshold that turns
that fraction into `degraded` is operator-configured, not asserted here.

`degraded` never blocks ingest. It is a label the operator can filter on.

### Security-relevant surfaces — routed, not designed here

The archive-bounds and path-safety checks above sit on a threat surface
(decompression bombs, XML entity expansion, path traversal, symlink escape,
and — once provider calls are added — request forgery through operator-supplied
URLs). The repository already carries prior hardening work in this area
(BL-088, BL-106). **This specification states the requirements but does not
propose a complete control set; the threat model for the network layer and the
archive parsers should be reviewed by a qualified security reviewer before the
provider stage ships.** Treat the bounds above as a floor, not a design.

---

## 8. Stage 4 — Filename normalization

### Rules (normative)

Applied in order. Every rule is a pure function; the whole chain is one function
`normalize_name(fields, config) -> str`.

| # | Rule |
|---|---|
| N-0 | **Provenance gate.** Only fields with corroboration status `identifier-exact` or `corroborated` may enter the template. Otherwise fall back to the normalized original stem. |
| N-1 | Unicode-normalize every field to **NFC**, then apply the configured transliteration policy (`fold` → ASCII via compatibility decomposition + diacritic strip; `keep` → retain NFC). Default `fold`. |
| N-2 | Casefold to lowercase. |
| N-3 | Replace any run of characters outside `[a-z0-9]` with a single `-` **inside** a field. |
| N-4 | Strip leading and trailing `-` from each field. |
| N-5 | Join fields with `_`. Omit empty fields and their separator — never emit `__` or a trailing `_`. |
| N-6 | Canonicalize the extension: lowercase, and map `.markdown → .md`, `.htm → .html`, `.text → .txt`. The mapped extension must still be in the allowlist. |
| N-7 | Truncate the stem so the **encoded UTF-8 filename** is ≤ 200 bytes, cutting at a `-` boundary where possible. (200, not 255, leaves room for the collision suffix and a copy marker.) |
| N-8 | Reject and re-derive if the stem matches a reserved device name (`con`, `prn`, `aux`, `nul`, `com1`–`com9`, `lpt1`–`lpt9`) or begins with `.` or `-`. |
| N-9 | **Collision breaking.** If the target name already exists (on disk or elsewhere in this manifest) with different content, append `-{sha256[:8]}` to the stem. If it exists with *identical* content, do not rename — emit `ALREADY_PRESENT`. |
| N-10 | The result must round-trip: `normalize_name(normalize_name(x)) == normalize_name(x)`. |

Rule N-9 uses the content hash rather than an incrementing counter on purpose:
a counter makes the output depend on directory iteration order, which breaks the
determinism characteristic and makes two runs of the same directory disagree.

### Template

Configurable via `DOCRAY_PREP_NAME_TEMPLATE`. Default:

```
{year}_{authors}_{title}_{id}.{ext}
```

- `{year}` — four-digit publication year, or omitted.
- `{authors}` — first author surname; `-etal` appended when the record lists more
  than the configured maximum. Omitted when unknown.
- `{title}` — normalized title, truncated to the configured word budget at a
  word boundary.
- `{id}` — `doi-10-1000-xyz` / `isbn-9780134757599` / `arxiv-2607-20638`, omitted
  when no exact identifier was resolved.
- `{ext}` — canonicalized extension.

Worked examples:

```
Fundamentals of Software Architecture 2nd ed.pdf
  → 2020_richards_fundamentals-of-software-architecture_isbn-9781492043454.pdf

2607.20638v2.pdf
  → 2026_hoe_a-systems-perspective-on-intake_arxiv-2607-20638v2.pdf

scan_0043 (1).pdf          [no identifier, no corroborated metadata]
  → scan-0043-1.pdf         [N-0 fallback: normalized original stem only]
```

The third example is the important one. A file the tool knows nothing about gets
a *tidier* name and nothing more. The tool never invents a title to fill a slot.

### Original name is preserved

`documents.original_filename` continues to record the name as it arrived,
pre-normalization. The original name is evidence about the file's provenance and
is frequently the only clue to where a document came from; the tool must not
destroy it. The manifest records both, and the rename journal records the
mapping for rollback.

---

## 9. Stage 2 — Identifier extraction

Ordered by decreasing reliability. Extraction stops at the first source that
yields a **syntactically valid** identifier of a given kind; all candidates are
recorded regardless.

1. **Embedded metadata** — PDF XMP / Info dictionary (`doi`, `WPS-ARTICLEDOI`),
   EPUB OPF `dc:identifier` (`urn:isbn:`, `urn:doi:`), OOXML `docProps/core.xml`.
2. **Filename patterns** — arXiv ids (reuse `normalize_arxiv_ref()`, already
   implemented), DOI, ISBN-10/13.
3. **Document text, first N pages** — DOI and ISBN regex over extracted text,
   bounded by page count and character budget.

### Validation before use

- **DOI**: must match the registered-prefix shape `10.\d{4,9}/\S+`. Syntactic
  validity only — existence is decided by the provider lookup.
- **ISBN-10 / ISBN-13**: **checksum must validate** (mod-11 for ISBN-10, mod-10
  for ISBN-13). ISBN-10 is converted to ISBN-13 (`978` prefix, checksum
  recomputed) so lookups and cache keys are uniform.
- **arXiv**: must satisfy the existing `_ID` pattern.

Checksum validation is what stops the text scanner from turning any 13-digit
number in a page footer into a lookup. An identifier that fails validation is
recorded as `rejected` with its reason and is never sent to a provider.

---

## 10. Stage 3 — Provider enrichment

### 10.1 Provider facts

All facts below were verified against live vendor documentation on 2026-07-29.
Items that documentation did not state are marked **unverified** rather than
filled in.

| Provider | Endpoint verified | Auth | Rate limits | Data licence |
|---|---|---|---|---|
| **Crossref REST** | Base `https://api.crossref.org/v1`; works endpoint for DOI metadata | None for the public and polite pools. Plus tier uses `Crossref-Plus-API-Token: Bearer <key>` | Public pool: 5 requests per interval, 1 concurrent. Polite pool: 10 per interval, 3 concurrent. Plus: 150 per interval. Live values are echoed in `x-rate-limit-limit`, `x-rate-limit-interval`, `x-concurrency-limit`; over-limit returns 429 | **Unverified** — the licence statement located covers the website content, not the API metadata. Confirm before redistribution |
| **OpenAlex** | Works lookup by DOI (see note) | **API key required since 2026-02-13.** Free key from `openalex.org/settings/api`. The `mailto` polite-pool convention has been **discontinued** | Free tier: 100,000 credits/day, credit-based — different endpoint classes cost different credit amounts | **CC0** (stated: "Our complete dataset is free under the CC0 license") |
| **Open Library** | `https://openlibrary.org/api/volumes/brief/isbn/{isbn}.json` (Read/Partner API); multi-lookup form `.../brief/json/{request-list}` with `\|`-separated requests | None | **1 request/second** unidentified; **3 requests/second** with a `User-Agent` carrying the application name and a contact email | **Unverified** — the licensing page states only that the Internet Archive "does not assert any new copyright or other proprietary rights over any of the material in the Open Library database", and names no licence |
| **Google Books** | `https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}` | **API key required** for public-data requests | **Unverified** — the "Using the API" guide states no quota numbers | **Unverified** — no caching or storage terms stated in the developer guide; check the API Terms of Service before caching |
| **DataCite** | `https://api.datacite.org/dois/{doi}` | Reads appear unauthenticated (documentation shows unauthenticated `curl`; **not stated explicitly**) | **Unverified** on this endpoint — a separate rate-limits document exists | CC-BY (site-wide "All content CC-BY") |
| **arXiv** | `https://export.arxiv.org/api/query?id_list={id}` — **already implemented** in this repository | None; policy asks clients to identify themselves via `User-Agent` | Not re-verified for this document | Not re-verified for this document |

Three of these — **Crossref, Open Library, DataCite** — need no API key and
satisfy the "at least three public providers" requirement on their own. OpenAlex
and Google Books are second-tier because each now requires a key, and a
capability that silently degrades when an unset key is missing is worse than one
that never claimed to be there. Both are shipped **disabled by default**, and
the tool reports which providers were skipped and why.

Two operational consequences follow directly from the verified numbers:

- **Open Library at 1 rps** is the binding constraint for book-heavy runs. A
  1,000-ISBN directory takes at least ~17 minutes against Open Library alone.
  This is why the response cache is a requirement, not an optimization.
- **OpenAlex's key requirement is recent** (February 2026). Any prior integration
  guidance that describes a key-free OpenAlex with a `mailto` polite pool is
  out of date.

> The exact OpenAlex works-by-DOI path was not confirmed from primary
> documentation in this pass; it must be read from the current API reference at
> implementation time rather than assumed. The same applies to the Crossref
> single-DOI path shape.

### 10.2 Routing

| Identifier kind | Primary | Corroborating | Fallback |
|---|---|---|---|
| arXiv id | arXiv Atom (existing adapter) | OpenAlex (if enabled) | — |
| DOI | Crossref | OpenAlex (if enabled) | DataCite (for DOIs Crossref does not hold) |
| ISBN-13 | Open Library | Google Books (if enabled) | — |
| None | — | — | Bibliographic probe (§10.4) |

A provider is queried only for identifier kinds it can answer. No provider is
ever asked to guess.

### 10.3 Reconciliation

Each provider returns a record normalized into a common shape: `title`,
`subtitle`, `authors[]`, `publication_year`, `publisher`, `language`,
`identifiers{}`, `page_count`, `edition`.

Every field is then assigned a **corroboration status**:

| Status | Condition |
|---|---|
| `identifier-exact` | Resolved by exact identifier lookup and returned by the primary provider |
| `corroborated` | Two or more **independent** providers agree after normalization |
| `single-source` | Exactly one provider supplied it |
| `conflicted` | Providers disagree |
| `absent` | No provider supplied it |

Field comparison normalizes before comparing: casefold, strip punctuation, NFC,
collapse whitespace; author comparison uses surname + first initial; year
comparison is exact.

**Conflicts are never silently resolved.** A `conflicted` field records every
variant with its provider and is excluded from the filename by N-0. The operator
resolves it, or it stays unresolved.

**Independence caveat.** Provider agreement is weaker evidence than it looks:
metadata providers ingest from overlapping upstreams, and two providers echoing
one registration agency are not two witnesses. The manifest records which
providers agreed so the operator can judge the agreement rather than trusting the
count. `corroborated` means "two providers said so", not "true".

### 10.4 The no-identifier case

When no identifier is found, the tool may run a bibliographic probe — a
title-and-author query against Crossref's bibliographic query and Open Library's
search — but every field it yields is capped at `single-source` and can never
reach `corroborated` on the strength of the probe alone. Probe results are
excluded from the filename by N-0 and are marked `unverified` in the manifest.
The probe is off by default (`--probe`).

### 10.5 Politeness and boundedness

Non-negotiable, and enforced in the shared HTTP layer rather than per adapter:

- A per-provider token-bucket limiter configured **at or below** the verified
  published rate for that provider.
- A `User-Agent` identifying doc-ray and carrying the operator contact address
  from `DOCRAY_PREP_CONTACT_EMAIL` — required by Open Library for its 3 rps tier,
  and one of the two ways into Crossref's polite pool.
- An on-disk response cache under `_ephemeral/prep-cache/`, keyed by canonical
  request URL, with a configurable TTL. Re-running the tool over the same
  directory must issue **zero** provider requests.
- Retry only on 429 and 5xx, with exponential backoff and jitter, honouring
  `Retry-After`; a hard cap on attempts per identifier.
- A per-run request ceiling. On reaching it the run completes with the remaining
  files marked `enrichment_skipped: budget_exhausted` — the manifest stays
  complete and honest instead of the run dying halfway.
- Any provider error degrades exactly one field set. It never fails the file, and
  it never fails the run.

---

## 11. The manifest

`intake-manifest.v1.json`, written to the staging root. One object, `schema:
"docray.intake-manifest/1"`, containing a run block and a record per file.

```jsonc
{
  "schema": "docray.intake-manifest/1",
  "run": {
    "tool_version": "0.1.0",
    "started_at": "2026-07-29T10:00:00Z",
    "root": "_ingest/",
    "providers_enabled": ["crossref", "openlibrary", "datacite"],
    "providers_skipped": [
      {"name": "openalex", "reason": "no_api_key"},
      {"name": "google_books", "reason": "no_api_key"}
    ],
    "request_count": 412,
    "cache_hits": 88
  },
  "files": [
    {
      "original_path": "_ingest/Fundamentals of Software Architecture 2nd ed.pdf",
      "content_sha256": "a3f1…",
      "byte_size": 18234112,
      "detected_extension": ".pdf",
      "quality": {
        "verdict": "ok",
        "checks": [
          {"code": "MAGIC_MATCH", "status": "pass"},
          {"code": "PDF_LOW_TEXT_COVERAGE", "status": "pass", "measured": 0.98}
        ]
      },
      "identifiers": [
        {"kind": "isbn13", "value": "9781492043454",
         "found_in": "embedded:pdf-info", "validation": "checksum_ok"}
      ],
      "metadata": {
        "title": {
          "value": "Fundamentals of Software Architecture",
          "status": "identifier-exact",
          "provenance": [
            {"provider": "openlibrary",
             "endpoint": "https://openlibrary.org/api/volumes/brief/isbn/9781492043454.json",
             "retrieved_at": "2026-07-29T10:00:04Z",
             "raw": "Fundamentals of Software Architecture"}
          ]
        },
        "publication_year": {
          "value": null,
          "status": "conflicted",
          "variants": [
            {"value": 2020, "providers": ["openlibrary"]},
            {"value": 2025, "providers": ["google_books"]}
          ]
        }
      },
      "proposed_name": "2020_richards_fundamentals-of-software-architecture_isbn-9781492043454.pdf",
      "name_basis": ["identifier-exact:title", "corroborated:authors"],
      "actions": ["rename", "ingest"]
    }
  ]
}
```

Required by schema on every enriched field: `status`, and either `provenance`
(one or more entries) or `variants`. A field carrying a value with neither fails
validation — this is the mechanism that makes an unsourced value impossible to
represent rather than merely discouraged.

`name_basis` records *which* facts licensed the proposed name, so a reviewer can
audit the rename without re-deriving it.

---

## 12. Integration with ingest

### 12.1 Rename and journal

`docray-prep apply --manifest intake-manifest.v1.json` renames files and writes
`intake-journal.v1.jsonl` — one line per rename with old path, new path, and
content hash. `docray-prep revert --journal …` reverses it.

`apply` **refuses to run** when any file's current content hash differs from the
hash the manifest recorded, or when a target path exists with different content.
Both conditions mean the manifest is stale, and a stale manifest applied to a
changed directory is how files get lost.

### 12.2 Feeding the corpus

`pipeline.ingest_paths` / `ingest_bytes` gain an optional manifest parameter.
When present:

- `title` uses the manifest title **only** when its status is `identifier-exact`
  or `corroborated`; otherwise the extractor-derived title stands. This mirrors
  the existing precedent — `ingest_bytes(…, title=…)` already accepts an
  authoritative source title from the arXiv adapter.
- `original_filename` records the **pre-normalization** name.
- Enriched fields and their full provenance land in `documents.metadata` under a
  namespaced key (`intake_manifest`), never overwriting curated values.
- Every field the manifest carries that is *not* applied is written as a
  **suggestion** through the existing BL-061 path
  ([document_metadata_suggestion_store](../../src/doc_ray/adapters/document_metadata_suggestion_store.py)),
  so it surfaces in the existing AI-suggest → human-decide workflow rather than
  inventing a second review surface.

### 12.3 Required change to the suggestion contract

`MetadataSuggestionVerifier.verify` currently allows only
`{"title", "tags", "duplicate"}`
([document_metadata_suggest.py:48](../../src/doc_ray/application/document_metadata_suggest.py#L48)).
Enrichment produces `author`, `publication_year`, `publisher`, `language`, and
`identifiers`. The field allowlist must widen, and the suggestion record must
carry a `source` distinguishing `provider:crossref` from the existing
`verified-deterministic`. This is a small, contained change to an existing
verified path — but it is a change to a **contract**, and it needs its own
migration for the stored `source` values plus a webapp column showing the
provider.

---

## 13. Code layout

Hexagonal, matching the existing structure. Every module stays under the
400 net-LOC cap (D-015).

```
src/doc_ray/
  ports/
    metadata_provider.py          # Protocol: name, supports(kind), lookup(kind, value)
  domain/
    intake_manifest.py            # dataclasses + schema version + validation
    filename_rules.py             # N-1…N-10 as pure functions
    identifiers.py                # DOI/ISBN/arXiv parse + checksum validation
  adapters/
    metadata_provider_crossref.py
    metadata_provider_openalex.py
    metadata_provider_openlibrary.py
    metadata_provider_google_books.py
    metadata_provider_datacite.py
    provider_http.py              # shared client: limiter, cache, UA, backoff
    intake_probe.py               # magic sniff + per-format integrity checks
  application/
    prepare.py                    # orchestration S1→S5
    metadata_resolve.py           # routing, fan-out, reconciliation, statuses
  entrypoints/
    prepare.py                    # CLI: scan | apply | revert | ingest
```

The arXiv adapter is **reused, not duplicated**:
`normalize_arxiv_ref()` moves to `domain/identifiers.py` and
`document_source_arxiv.py` imports it from there.

---

## 14. Delivery slices

Each slice is independently useful and independently shippable.

| # | Slice | Complexity | Delivers |
|---|---|---|---|
| 1 | Probe + quality gate + manifest schema, CLI `scan` | M | Operator can triage a directory. No network, no rename. |
| 2 | Filename normalization + `apply` / `revert` | M | Reversible renaming from a reviewed manifest. |
| 3 | Identifier extraction + Crossref + Open Library + DataCite | L | Three key-free providers, reconciliation, provenance. |
| 4 | Ingest integration + suggestion-contract widening | M | Enriched metadata reaches the corpus as reviewable proposals. |
| 5 | OpenAlex + Google Books behind keys; bibliographic probe | S | Optional depth for operators who configure keys. |

Slice 1 alone answers the question the operator most often has ("is this
directory ingestible?") and requires no external dependency at all.

---

## 15. Configuration

New variables, all registered in `config/env.registry.yaml` (the
`scripts/gen_env_samples.py --check` gate fails otherwise).

| Variable | Default | Secret | Purpose |
|---|---|---|---|
| `DOCRAY_PREP_CONTACT_EMAIL` | `''` | no | Contact address in the `User-Agent` and Crossref `mailto`. Empty ⇒ providers requiring identification run at their unidentified rate |
| `DOCRAY_PREP_PROVIDERS` | `crossref,openlibrary,datacite` | no | Ordered allowlist of enabled providers |
| `DOCRAY_PREP_OPENALEX_API_KEY` | `null` | **yes** | Free key; absent ⇒ OpenAlex skipped and reported |
| `DOCRAY_PREP_GOOGLE_BOOKS_API_KEY` | `null` | **yes** | Absent ⇒ Google Books skipped and reported |
| `DOCRAY_PREP_CACHE_DIR` | `_ephemeral/prep-cache` | no | Response cache (gitignored; never `/tmp`) |
| `DOCRAY_PREP_CACHE_TTL_SECONDS` | `2592000` | no | 30 days |
| `DOCRAY_PREP_HTTP_TIMEOUT_SECONDS` | `30` | no | Per-request timeout |
| `DOCRAY_PREP_MAX_REQUESTS_PER_RUN` | `5000` | no | Hard external-I/O ceiling |
| `DOCRAY_PREP_NAME_TEMPLATE` | `{year}_{authors}_{title}_{id}` | no | Filename template |
| `DOCRAY_PREP_TRANSLITERATE` | `fold` | no | `fold` or `keep` |
| `DOCRAY_PREP_MAX_FILE_BYTES` | `536870912` | no | 512 MiB reject threshold |

---

## 16. How this fails

| Failure | Signal that precedes it | Guard |
|---|---|---|
| **Laundered guess.** An unverified provider title becomes a filename, then a title suggestion, then a corpus title | Files acquire confident names the operator cannot trace | N-0 provenance gate; `name_basis` in the manifest; suggestion path never auto-applies |
| **Silent degradation.** A provider key is unset, half the metadata never arrives, and nothing says so | Enrichment quality varies by machine | `providers_skipped` with reasons in every manifest run block |
| **Rate-limit block.** A large run gets the operator's IP blocked | 429s climbing in the run log | Limiter set at or below verified published rates; cache; per-run ceiling; no retry storm |
| **Stale manifest applied.** Directory changed between `scan` and `apply`; renames land on the wrong files | None until files are lost | `apply` verifies every recorded content hash before touching anything |
| **Sound but unbuildable.** The design is fine and the rate limits make it unusable at the operator's corpus size | Estimated wall-clock per 1,000 files | Recorded up front: Open Library's 1 rps floor is the binding constraint (§10.1) |
| **Conflict flattened.** Two providers disagree on a year; the tool picks one | A metadata field that looks certain and is not | `conflicted` status; excluded from the name; every variant retained |

---

## 17. What is measured, and what is not

Two conceptual measures are worth recording per run. **No target value is
proposed for either** — thresholds are the operator's to set against a real
corpus, and asserting a number here would be inventing one:

- **Enrichment coverage** — the fraction of scanned files for which at least one
  field reached `identifier-exact` or `corroborated`.
- **Provenance completeness** — the fraction of populated metadata fields
  carrying at least one provenance entry. This one has a defensible target:
  schema validation makes it 1.0 by construction, so any value below 1.0 is a bug
  in the writer, not a quality signal.

Also recorded, as diagnostics rather than quality measures: request count, cache
hit rate, per-provider 429 count, and quality-verdict distribution.

---

## 18. Testing

Test-first, per repository standard. The gates in `CLAUDE.md` §"Landing
checklist" all apply.

- **Pure-function tests** (no I/O) for every N-rule, including idempotence
  (N-10), the reserved-name guard, byte-length truncation on multi-byte titles,
  and hash-based collision breaking.
- **Identifier tests**: valid/invalid ISBN-10 and ISBN-13 checksums, ISBN-10 →
  ISBN-13 conversion, DOI shape, arXiv old- and new-style ids.
- **Provider adapter tests** against `httpx.MockTransport` — success, 404, 429
  with `Retry-After`, 5xx, malformed JSON, and empty result — following the
  existing arXiv adapter's test pattern. **No test performs live network I/O.**
- **Reconciliation tests**: agreement, disagreement, single source, and the
  normalization rules that decide whether two titles are "the same".
- **Quality-gate fixtures**: an encrypted PDF, a scanned (image-only) PDF, a
  zip with an invalid EPUB container, a truncated DOCX, a non-UTF-8 text file,
  and a file whose extension lies about its magic bytes.
- **Round-trip test**: `scan` → `apply` → `revert` restores every original name.
- **Stale-manifest test**: mutating a file after `scan` makes `apply` refuse.
- **Determinism test**: two `scan` runs over the same directory with a warm cache
  produce byte-identical manifests apart from the run timestamps, and issue zero
  provider requests on the second run.

---

## 19. Decisions the operator must make

Four, and the specification cannot resolve them:

1. **Does a `degraded` verdict block ingest?** Recommendation: no — label and
   continue. But if scanned PDFs entering the corpus is considered a defect
   rather than a known limitation, this becomes a `reject` and slice 1's value
   changes.
2. **Google Books caching terms.** Its developer guide states no caching or
   storage terms; the response cache stores provider responses on disk. Someone
   must read the API Terms of Service before slice 5 ships, or Google Books
   stays out.
3. **Crossref and Open Library metadata licensing.** Neither documentation page
   located names a licence for the *metadata*. If enriched fields will be
   redistributed (exports, the public webapp, MCP responses), the licence
   position needs confirming — it is not a blocker for private corpus use.
4. **Where staging lives.** `_ingest/` is already gitignored and is the obvious
   default; confirm before it is written into the tool's defaults.

---

## 20. Backlog entries

Landed in [`docs/backlog.yml`](../backlog.yml) and indexed in
[`docs/BACKLOG.md`](../BACKLOG.md). Each carries its own acceptance criteria;
they are not restated here, because a second copy of a specification is a second
thing to keep in sync.

| Id | Slice | Category | Status | Complexity |
|---|---|---|---|---|
| BL-189 | 1 — quality gate and manifest | extraction | ready | M |
| BL-190 | 2 — filename normalization, apply/revert | extraction | ready | M |
| BL-191 | 3 — Crossref, Open Library, DataCite with provenance | extraction | ready | L |
| BL-192 | 4 — widen the suggestion contract | persistence | ready | M |
| BL-193 | Format-list drift found while writing this spec | extraction | **done** | S |

BL-193 was not planned work. Writing §7's format table surfaced that the
extension allowlist had been re-typed on eight surfaces and every copy had
drifted behind the extractor registry — the upload form offered five extensions
where eleven ingest. It is fixed: the allowlist now has one definition in
`domain/document_formats.py`, the registry is asserted against it, and the
upload form renders from it.

Slice 5 (OpenAlex and Google Books behind API keys, plus the bibliographic
probe) has no backlog entry yet. It is gated on the two licensing questions in
§19, and filing it before those are answered would record work that may not be
allowed to ship.

---

## 21. Proposed decision record

For `engineering_decisions.yml`. **Not yet written**, deliberately: the decision
format requires each claim to carry executable checks, and the tests those
checks name do not exist until slice 1 lands. A decision record whose checks
cannot run is a red gate, not a record. It lands with slice 1.

Allocate the next free id at that point, against the registry's own head rather
than against this document: the last id present in `engineering_decisions.yml`
is D-036, and the one after it is already spoken for by the in-flight MCP work.

**Title.** Enriched intake metadata is a proposal with provenance, never an
asserted fact.

**Status.** Proposed.

**Context.** Intake preparation introduces third-party bibliographic providers
as a new dependency class on the ingest path. Provider output is external
assertion, subject to error, staleness, and disagreement. doc-ray's stated
position is that claims must be traceable to sources and that machine-derived
values are proposals until a human confirms them. Filenames are an unusually
dangerous carrier for an unverified value, because the codebase already derives
title suggestions from filenames — so an invented name becomes an invented
title one layer later.

**Decision.**

1. Every enriched metadata field carries a corroboration status and provider-level
   provenance. A field carrying a value with neither is a schema violation, not a
   lint warning.
2. Only `identifier-exact` and `corroborated` fields may enter a proposed
   filename. Everything weaker falls back to the normalized original stem.
3. Conflicting provider values are recorded as variants and never auto-resolved.
4. Enriched values reach `documents` only through the existing suggestion path,
   under human decision — never by direct write.
5. `original_filename` always records the pre-normalization name.

**Consequences.**

- *Accepted cost:* the tool will frequently produce a tidier version of the
  original name and nothing more. That is the correct behaviour for a file it
  cannot identify, and it will look like under-delivery to anyone expecting every
  file to come out with a full bibliographic name.
- *Accepted cost:* two providers agreeing is treated as corroboration even
  though metadata providers share upstreams. The manifest records which providers
  agreed so the operator can judge the agreement; the tool does not model
  provider independence.
- *Gained:* every corpus title traceable to either an extractor or a cited
  provider response, and no path by which a guess becomes a fact.

**Compliance.** Manifest schema validation rejects unsourced values. Tests assert
that a file with no corroborated metadata is renamed from its own stem only.

---

## 22. Sources

Verified 2026-07-29 against live vendor documentation:

- Crossref REST API — access tiers, pools, rate limits: <https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/>
- Crossref REST API — usage guidance: <https://www.crossref.org/documentation/retrieve-metadata/rest-api/tips-for-using-the-crossref-rest-api/>
- OpenAlex — API key requirement from 2026-02-13, discontinued polite pool, credit-based limits: <https://groups.google.com/g/openalex-users/c/rI1GIAySpVQ> and <https://developers.openalex.org/>
- OpenAlex — rate limits and authentication reference: <https://github.com/ourresearch/openalex-docs/blob/main/how-to-use-the-api/rate-limits-and-authentication.md>
- Open Library — API index and rate limits (1 rps / 3 rps identified): <https://openlibrary.org/developers/api>
- Open Library — Read/Partner API endpoint format: <https://openlibrary.org/dev/docs/api/read>
- Open Library — licensing statement: <https://openlibrary.org/developers/licensing>
- Google Books API — volumes endpoint and key requirement: <https://developers.google.com/books/docs/v1/using>
- DataCite REST API — single-DOI endpoint: <https://support.datacite.org/docs/api-get-doi>

Claims marked **unverified** in §10.1 are those the located documentation did
not state. They must be confirmed at implementation time, not assumed.
