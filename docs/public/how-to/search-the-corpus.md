---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Search the corpus"
doc_type: "how-to"
audience: ["end-user", "integrator"]
surfaces: ["webapp", "mcp", "api"]
classification: "public"
applies_to: "1.57.3"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Search the corpus

Find Documents, passages, and images with the retrieval mode that matches your
question: literal keyword search, learned semantic search, or cross-modal visual
search.

## Prerequisites

- A readable Corpus. Every search is scoped to the Documents you own or that are shared with you.
- **MCP:** an MCP client pointed at `/mcp` with a bearer token. Search tools are read tools and are always available.
- Use **full-text search** to pick a Document, **keyword Sentence search** for an
  exact phrase, **semantic search** for meaning, and **image search** for visual
  prior art. Prefer rare, specific terms for keyword search: a common word can
  flood one region and starve the rest.

## Choose a search mode

- **Full-text search** ranks whole Documents by relevance and returns one row per Document (`documentId`, `title`, `rank`) with no passage text. It answers "which Document?".
- **Sentence search** returns keyword-in-context rows — a `pre`/`hit`/`post` window around each match, with an `ordinal` citation handle. It answers "where inside the Document?".
- **Semantic search** ranks Sentences by learned cosine similarity, so wording
  may differ from the query. Every row still carries a Document and Sentence
  ordinal for verification.
- **Image search** embeds the query through the vision model's text tower and
  ranks visible Document images. It is discovery, not a factual assertion.

## Search through the Corpus browser

1. Open **Search** (`/search`) and choose **Keyword**, **Meaning**, or **Images**.
2. Enter a query and submit.

   Result: Keyword and Meaning list citable Sentences; Images shows RLS-visible
   thumbnails with Document title, page, class, and similarity.

3. Refine a flooded result by adding a more distinctive term rather than a common one.

   Result: matches concentrate on the passages that actually discuss the topic.

If Meaning has no usable vectors or the API is unavailable, the page states the
fallback and shows keyword matches. Image search reports the visual layer as
unavailable rather than silently presenting an empty corpus.

## Meaning-based search through MCP

```
semantic_search_sentences  query="water governance under scarcity"  limit=20
```

Searches the whole corpus. Add `document_id` only when you already know the scope
and want to rank within one work:

```
semantic_search_sentences  query="water governance under scarcity"  document_id="…"  limit=20
```

Result: ranked Sentence rows with `documentId`, `ordinal`, text, and similarity.
Use the ordinal with `read_document_sentences` to verify the surrounding passage.
If the tool returns `unsupported`, the active model is unavailable on that
deployment. If it returns no rows, ask an operator to verify corpus embedding
coverage.

For visual prior art:

```
cross_modal_image_search  query="desert irrigation schematic"  limit=25
```

Result: ranked image rows with Document and page provenance. Open the Document
gallery in the webapp to inspect the actual image bytes and committed caption.

## Sentence search through MCP

`search_sentences` returns `{hits, coverage, note}`. Its default
`match="substring"` is unchanged literal containment. Choose `match="phrase"`
when words, order, and boundaries must all agree. Read `coverage` and `note`:
`limit_reached` can mean either a full hit page or that phrase verification hit
its independent candidate cap.

Before spending a phrase search, batch candidate terms through the pre-flight:

```
term_frequency  terms=["Algorithm Design","algorithm","shared_buffers"]
```

For a phrase/compound row, `sentenceHits` is a bounded candidate count;
`occurrenceSentences` and `occurrences` are the `surface_word_v1` verified
sentence and overlap-aware occurrence counts. Choose using `floodRisk`, which is
based on `occurrenceSentences`, not substring false positives. If `capped` is
true, all three counts are floors and the result is incomplete/high-risk. A
capped verified zero leaves `attested` null rather than claiming absence. A
single indexed word keeps the exact lexeme path and has null scan fields.

1. Search a rare term, optionally scoped to one Document:

   ```
   search_sentences  term="spice melange"  limit=20
   ```

   Result:

   ```json
   {
     "hits": [
       {"documentId":"22222222-2222-2222-2222-222222222222","ordinal":184,
        "pre":"…the harvesters returned with the ","hit":"spice melange","post":" that the Guild depends on.","cursor":"…"}
     ],
     "coverage": {"hit_count":7,"ordinal_min":184,"ordinal_max":902,"limit":20,"limit_reached":false},
     "note": null
   }
   ```

2. Read the coverage signal. A `note` of `null` and `limit_reached: false` mean the result is complete for that term. When `limit_reached` is true, narrow the term or read beyond `coverage.ordinal_max`:

   Result (flooded query):

   ```json
   {
     "coverage": {"hit_count":20,"ordinal_min":12,"ordinal_max":41,"limit":20,"limit_reached":true},
     "note": "Returned the maximum 20 hits, spanning ordinals 12–41. A common term can flood one region and hide the rest — narrow with a rarer, scene-specific term, or use read_document_sentences by ordinal range (e.g. beyond 41) to cover the untouched text."
   }
   ```

   A sparse-looking result with a `note` is a query problem, not missing content.

3. Ask the exact whole-word phrase question when substring false positives matter:

   ```
   search_sentences  term="Algorithm Design"  match="phrase"  limit=20
   ```

   ```json
   {
     "matchProfile":"surface_word_v1",
     "hits":[{"ordinal":752,"hit":"Algorithm  Design","occurrenceCount":1,
       "occurrences":[{"charStart":18420,"charEnd":18437}]}],
     "coverage":{"hit_count":1,"limit_reached":false,"exhaustive":true,
       "candidate_scanned":4,"candidate_limit":2001,
       "candidate_limit_reached":false},
     "note":null
   }
   ```

   Offsets are absolute half-open positions in the original Document, including
   its original whitespace and typographic punctuation. `ngram` will not match
   `Tangram`; `algorithm design` will not match `algorithm designed`. If
   `candidate_limit_reached` is true, narrow to a longer or rarer phrase.

4. Page more hits by passing the last row's `cursor` as `after`. Stop when `hits` is empty.

## Picking a Document first, over MCP

```
full_text_search  query="water rationing on Arrakis"  limit=10
```

Result:

```json
[
  {"documentId":"22222222-2222-2222-2222-222222222222","title":"Field Notes","rank":0.61},
  {"documentId":"33333333-3333-3333-3333-333333333333","title":"Ecology Report","rank":0.44}
]
```

Take a `documentId` from this list into `search_sentences` (scoped with `document_id`) or into [Read a document](/docs/how-to/read-a-document).

## Verification

- A substring Sentence search returns `hits` whose `pre`/`hit`/`post` windows contain the term. A phrase search additionally returns every exact absolute occurrence and names `surface_word_v1`. Each `ordinal` addresses the same Sentence when read back with the reader.
- A full-text search returns Documents in descending `rank`.
- A semantic or image hit resolves back to an RLS-visible Document and an exact
  Sentence ordinal or page image.

## Common failures

- **A sparse result for a common term** — the term flooded one region. Read `note` and narrow the query. See [Search returns too few results](/docs/troubleshooting/result-truncated).
- **Cursor rejected** — a Sentence-search cursor is not valid in the reader; cursors are per-tool. See [Cursor not accepted](/docs/troubleshooting/cursor-not-interchangeable).
- **Empty results** — the Corpus you can read contains no match, or the term is too specific. Broaden the term or confirm the Document is visible to you.
- **Meaning is empty but Keyword finds text** — the active learned embeddings are
  missing for those Documents. Ask an operator to run the corpus backfill and
  verify its coverage report.
- **Images says unavailable** — the vision encoder or image embeddings are not
  installed for this deployment; do not treat this as proof that the corpus has
  no images.

## Next steps

- [Read a document](/docs/how-to/read-a-document)
- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about)
- Concept: [Sentence](/docs/concepts/sentences), [Span](/docs/concepts/spans)
- Reference: [MCP server](/docs/reference/mcp), [GraphQL API](/docs/reference/api)
