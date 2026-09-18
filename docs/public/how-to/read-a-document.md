---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 via Claude Code"
  date: "2026-07-25"
title: "Read a document"
doc_type: "how-to"
audience: ["end-user", "integrator"]
surfaces: ["webapp", "mcp"]
classification: "public"
applies_to: "1.57.4"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Read a document

Read a Document verbatim by first mapping it into addressable ordinal ranges, then reading any range — or the whole Document in order — with exact citation handles on every Sentence.

## Prerequisites

- Read access to the Document (you own it or it is shared with you).
- The Document's id. Get it from [Search the corpus](/docs/how-to/search-the-corpus) or by listing your Corpus.
- **MCP:** an MCP client pointed at `/mcp` with a bearer token. The reader tools are read tools and are always available.
- The reader returns Sentences in `ordinal` order, each carrying `charStart`/`charEnd` offsets, so any passage stays attributable whether read as a slice or in full.

## Read through the Corpus browser

1. Open the Document from the Corpus (`/corpus`), or navigate to `/doc/<document-id>`.

   Result: the Document page shows its title, metadata, and text.

2. Use the **Images** section to inspect extracted page images, Tier-1 class
   coverage, and committed captions. Selecting a thumbnail opens the RLS-checked
   image byte route; a pending Tier-3 proposal is never shown as a committed
   caption.

3. If the gallery says **backfill not run**, **partial**, or **unknown**, treat
   that as a coverage state. It is not evidence that the original Document has
   no visual content.

4. Scroll or page through the text to read it in order.

## Read through MCP

### Step 1 — Map the Document

`read_document_outline` returns addressable ranges. Each entry's `ordinalStart`/`ordinalEnd` goes straight back into the reader, so you never guess an ordinal.

```
read_document_outline  document_id="22222222-2222-2222-2222-222222222222"  max_entries=50
```

Result:

```json
{
  "source": "headings",
  "totalSentences": 1240,
  "entryCount": 9,
  "note": null,
  "entries": [
    {"level":1,"title":"Introduction","ordinalStart":0,"ordinalEnd":120,"charStart":0,"charEnd":6800,"pageStart":1,"pageEnd":4},
    {"level":1,"title":"Findings","ordinalStart":121,"ordinalEnd":880,"charStart":6801,"charEnd":51200,"pageStart":5,"pageEnd":33}
  ]
}
```

Read `source` before trusting the titles: `headings` is detected structure; `uniform` is an even navigation grid whose titles are not chapter names.

### Step 2 — Read a range

Pass an entry's `ordinalStart`/`ordinalEnd` as `from_ordinal`/`to_ordinal`:

```
read_document_sentences  document_id="22222222-2222-2222-2222-222222222222"  from_ordinal=121  to_ordinal=200  response_format="concise"
```

Result:

```json
[
  {"ordinal":121,"text":"The survey covered three regions.","charStart":6801,"charEnd":6834},
  {"ordinal":122,"text":"Each was sampled twice.","charStart":6835,"charEnd":6858}
]
```

Every row's `ordinal` and `charStart`/`charEnd` are the citation handle for that Sentence. Use `response_format="concise"` for bulk reading — same text and offsets, roughly a third fewer characters.

### Step 3 — Read the whole Document in order (optional)

Walk sequentially for complete coverage: start with `after` unset, pass the last row's `cursor` as `after`, and stop when the result is empty.

```
read_document_sentences  document_id="22222222-2222-2222-2222-222222222222"  limit=200  response_format="concise"
```

Result: the first 200 Sentences, the last carrying a `cursor` for the next page. An empty result marks the end of the Document.

## Verification

- The outline's `totalSentences` is the upper bound for ordinals; a ranged read within it returns Sentences whose `ordinal` values fall in the requested `from_ordinal`/`to_ordinal` span.
- Reading the same range twice returns identical text and offsets.

## Common failures

- **Empty result for a valid-looking id** — the Document is unknown or not readable with your token. See [Not found](/docs/troubleshooting/not-found).
- **Cursor rejected** — a Sentence-search cursor is not valid here; page the reader with its own `cursor`, or address a slice with `from_ordinal`/`to_ordinal`. See [Cursor not accepted](/docs/troubleshooting/cursor-not-interchangeable).
- **Coarse outline** — `source: "uniform"` means no headings were detected; the ranges still aim a read, but the titles are not chapter names.

## Next steps

- [Search the corpus](/docs/how-to/search-the-corpus)
- [Find who and what a document is about](/docs/how-to/find-who-and-what-a-document-is-about)
- Concept: [Sentence](/docs/concepts/sentences), [Span](/docs/concepts/spans)
- Reference: [MCP server](/docs/reference/mcp)
