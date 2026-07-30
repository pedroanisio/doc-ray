---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "OpenAI GPT-5 via Codex"
  date: "2026-07-25"
title: "Build a cited briefing"
doc_type: "how-to"
audience: ["end-user", "developer", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "1.57.0"
owner: "documentation-maintainer"
last_reviewed: "2026-07-29"
status: "current"
---

# Build a cited briefing

Assemble an RLS-scoped evidence pack for a topic, inspect every supporting
passage, and carry its citation handles into a downstream document.

## Prerequisites

- Read access to the Documents that may contribute evidence.
- A focused topic. The briefing retrieves by full-text and keyword fan-out in
  this release; it does not use semantic similarity yet.
- For API or MCP use, an authenticated read token. No write gate is required.

## Build a briefing in the webapp

1. Open **Briefing** (`/briefing`).
2. Enter a topic and submit.

   Result: the page shows visible source Documents, exact key passages, entity
   frequencies, coverage counts, retrieval notes, and explicit gaps.

3. Open each citation and confirm the passage supports the claim you intend to
   make.
4. If `gaps` reports a cap or no supporting passage, narrow the topic or state
   the limitation. Do not fill it from memory.

## Build the same pack through MCP

```
briefing_pack  topic="governance of scarce water"  max_documents=6  passages_per_document=4  max_entities=25
```

The response includes:

- `sources` — ranked Documents with Document-level citation handles;
- `key_passages` — exact Sentence text, ordinal, matched terms, and `cite`;
- `entities` — merged, filtered entity-frequency evidence;
- `coverage` and `gaps` — what the bounded pack did and did not return;
- `guidance` — the rule for downstream synthesis.

## Query the GraphQL field

```graphql
query Briefing($topic: String!) {
  briefingPack(
    topic: $topic
    maxDocuments: 6
    passagesPerDocument: 4
    maxEntities: 25
  ) {
    topic
    retrieval
    guidance
    gaps
    coverage { documents passages }
    sources { documentId title rank cite { ref } }
    keyPassages { documentId sentenceOrdinal text matchedTerms cite { ref } }
    entities { text entityType count }
  }
}
```

GraphQL and MCP delegate to the same deterministic application service. Field
casing differs at the adapter boundary, but ordering, evidence selection,
budget gaps, and citation content remain aligned.

## Verification

- Every asserted point resolves to a `keyPassages[].cite` entry.
- `coverage.documents` equals the number of returned sources, and
  `coverage.passages` equals the number of returned key passages.
- Any response-budget or no-match condition appears in `gaps`; it is never
  silently discarded.

## Common failures

- **No Documents match** — confirm the topic wording and your RLS visibility.
- **A source has no key passage** — the Document matched at full-text level but
  none of the bounded keyword probes produced a passage; the gap states this.
- **Expected a semantic match** — semantic retrieval is not part of briefing
  composition in 1.43.0. Use [Search the corpus](/docs/how-to/search-the-corpus)
  to discover semantically related passages, then cite them separately.
- **The pack is capped** — lower fan-out or run several narrower briefings;
  never describe a capped sample as the complete record.

## Next steps

- [Read a document](/docs/how-to/read-a-document)
- [Search the corpus](/docs/how-to/search-the-corpus)
- Reference: [GraphQL API](/docs/reference/api), [MCP server](/docs/reference/mcp)
