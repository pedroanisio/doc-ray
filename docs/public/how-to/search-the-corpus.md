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
applies_to: "1.41.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-25"
status: "current"
---

# Search the corpus

Find the right Document with a full-text search, then locate exact passages inside it with a keyword-in-context Sentence search, reading the coverage signal to tell a query problem from missing content.

## Prerequisites

- A readable Corpus. Every search is scoped to the Documents you own or that are shared with you.
- **MCP:** an MCP client pointed at `/mcp` with a bearer token. Search tools are read tools and are always available.
- Two searches serve two goals. Use **full-text search** to pick a Document out of the Corpus. Use **Sentence search** to find passages inside Documents. Prefer **rare, specific terms**: a common word floods one region of a Document and starves the rest.

## Full-text search versus Sentence search

- **Full-text search** ranks whole Documents by relevance and returns one row per Document (`documentId`, `title`, `rank`) with no passage text. It answers "which Document?".
- **Sentence search** returns keyword-in-context rows — a `pre`/`hit`/`post` window around each match, with an `ordinal` citation handle. It answers "where inside the Document?".

## Search through the Corpus browser

1. Open **Search** (`/search`).
2. Enter a rare, specific term and submit.

   Result: the page lists matching Sentences, each shown in context with its Document title.

3. Refine a flooded result by adding a more distinctive term rather than a common one.

   Result: matches concentrate on the passages that actually discuss the topic.

## Sentence search through MCP

`search_sentences` returns `{hits, coverage, note}`. Read `coverage` and `note`: when `limit_reached` is true, a common term may have flooded one region, and `note` steers you to a rarer term or to reading the untouched ordinal ranges directly.

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

3. Page more hits by passing the last row's `cursor` as `after`. Stop when `hits` is empty.

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

- A Sentence search returns `hits` whose `pre`/`hit`/`post` windows contain the term, and each `ordinal` addresses the same Sentence when read back with the reader.
- A full-text search returns Documents in descending `rank`.

## Common failures

- **A sparse result for a common term** — the term flooded one region. Read `note` and narrow the query. See [Search returns too few results](/docs/troubleshooting/result-truncated).
- **Cursor rejected** — a Sentence-search cursor is not valid in the reader; cursors are per-tool. See [Cursor not accepted](/docs/troubleshooting/cursor-not-interchangeable).
- **Empty results** — the Corpus you can read contains no match, or the term is too specific. Broaden the term or confirm the Document is visible to you.

## Next steps

- [Read a document](/docs/how-to/read-a-document)
- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about)
- Concept: [Sentence](/docs/concepts/sentences), [Span](/docs/concepts/spans)
- Reference: [MCP server](/docs/reference/mcp), [GraphQL API](/docs/reference/api)
