---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-24"
---

# Improving the doc·ray MCP layer

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Where this comes from

A user summarized *Dune* end-to-end through the doc·ray MCP server and wrote up
the experience. The tool worked — every claim traced to a source — but there was
a real learning curve, and **most of the friction was the agent re-learning
things the server could have taught it up front**. That is the exact class of
problem the current best practice literature addresses: the fix for "the agent
queried badly" is rarely a new capability — it is a tool that *steers* the agent
toward the right strategy through its descriptions, its return shape, and its
error messages.

The recommendations below map each piece of feedback to a concrete change,
grounded in the [MCP specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
and Anthropic's [*Writing effective tools for AI agents*](https://www.anthropic.com/engineering/writing-tools-for-agents).

## The feedback, distilled

| # | Symptom | Root cause |
|---|---|---|
| A | High-frequency terms ("Paul", "Arrakis") flooded results and starved the back half of the book; the agent mis-read a **retrieval** failure as **missing content**. | Search returns hits but no *coverage* signal, and nothing warns that a term is high-frequency. The agent has to learn the failure mode by hitting it. |
| B | NER was noisy — "Duncan Idaho" split into a person + a US state; "Arrakis" scattered across four entity types — and needed manual reconciliation. | Entity tools return **raw NER**, not doc·ray's already-computed **canonical** identities, and carry no confidence. |
| C | The exhaustive reader is faithful but ~200 sentences ≈ 18k tokens, so reading a whole 18k-sentence novel blows the context window many times over. Random access (`from_ordinal`/`to_ordinal`) is what made a complete summary tractable. | Reach was solved (ranged read shipped mid-session), but the agent still lacks a **map** to aim ranged reads, and every read is "detailed" (no concise mode). |
| D | Cursors are not interchangeable — a `search_sentences` cursor is rejected by `read_document_sentences` — which cost a failed call. | The rejection is a bare error, not a steer. Per MCP, cursors are opaque **per tool**; the boundary is correct, but the error doesn't teach it. |
| E | There is no MCP **resources** layer — only tools — so no clean URI-addressed reads. | doc·ray exposes tools only; it never registered resources or resource templates. |
| F | "I'd have gotten there faster by reaching for the ranged reader earlier instead of grinding keyword searches." | The *methodology* (outline → ranged read → rare-term search) lives in the user's head, not in the server. |

## The organizing principle

Anthropic's finding is that **tool descriptions and return shapes are
load-bearing** — "even small refinements … can yield dramatic improvements,"
and the highest-leverage move is to make tools *self-teaching* through
descriptions that say when **not** to use them and error messages that give
"specific and actionable improvements, rather than opaque error codes." Almost
every item of feedback here is a self-teaching gap, not a missing feature. So the
work is ordered cheapest-and-highest-leverage first.

The MCP primitives also map cleanly onto the gaps
([Microsoft](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/mcp-demystified-tools-vs-resources-vs-prompts-explained-simply/4508057),
[WorkOS](https://workos.com/blog/mcp-features-guide)): **tools** are
model-controlled actions (what doc·ray has), **resources** are
application-controlled URI-addressed reads (gap E), and **prompts** are
user-selected named workflows (gap F).

---

## P0 — Make the server teach the model (days, not weeks of user learning)

> **SHIPPED in 1.39.0 (BL-145).** All three items below are implemented, tested,
> and documented — see [`docs/mcp-service.md` §3.1](../mcp-service.md#31-self-teaching-surface--instructions-coverage-cursors-bl-145)
> for the delivered contract and `src/doc_ray/tests/test_mcp_p0.py` for the tests.
> Two deltas from the proposal as written:
> - **Coverage fields.** The shipped shape is
>   `{hit_count, ordinal_min, ordinal_max, limit, limit_reached}`. The proposed
>   `document_ordinals` / `covered_fraction` were dropped: computing them costs a
>   second query for the document's sentence count on every search, and
>   `limit_reached` already answers the question the agent actually has ("is this
>   result capped?"). The fraction can return with the outline tool (P1 #5), which
>   fetches document structure anyway.
> - **Instructions text.** Written against the *shipped* toolset, so it names
>   `list_documents` rather than `read_document_outline` (P1, not yet built).
>
> Also shipped alongside: a **drift guard** (`test_mcp_p0.py`) failing the build
> if any registered tool has a description under 80 characters or skips local
> `document_id` validation, so new surface inherits this treatment automatically.

### 1. Add a server `instructions` block
`server.py` builds `FastMCP("doc-ray")` with **no instructions**. FastMCP exposes
an `instructions=` argument whose text the client surfaces to the model as
"how to use this server." Put the whole learning curve there, once:

> doc·ray is **retrieval, not reading** — it returns cited slices, not whole
> documents. To cover a document completely and cheaply: (1) `read_document_outline`
> to see its structure and ordinal ranges; (2) `read_document_sentences` with
> `from_ordinal`/`to_ordinal` to read any section verbatim; (3) `search_sentences`
> with **rare, specific** terms to locate particular beats — common words flood
> and starve coverage. Prefer the ranged reader over grinding keyword searches.

This is the single highest-leverage change: it front-loads gaps A, C, and F for
every agent, at the cost of one string. (Anthropic: description refinements drove
SOTA on SWE-bench.)

### 2. Return a **coverage signal** from `search_sentences` (kills the false "missing content")
Today the tool returns hits. Make it also return where the hits fall across the
document's ordinal span, and steer when they cluster:

```jsonc
{
  "hits": [ … ],
  "coverage": { "document_ordinals": 18128, "hit_ordinal_min": 12, "hit_ordinal_max": 1904,
                "covered_fraction": 0.10 },
  "note": "42 hits, all in the first 10% of the document. A high-frequency term can
           flood one region and hide the rest — try a rarer, scene-specific term, or
           read_document_sentences by ordinal range for the untouched regions."
}
```

The agent that saw "whole scenes are missing" would instead see *"your hits are
all in chapter 1"* — a retrieval diagnosis, not a false content diagnosis. This
is the "return high-signal context" principle plus the agentic-retrieval finding
that **coverage is a structural requirement**
([Azure AI Search](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

### 3. Turn the cross-tool cursor rejection into a steer (gap D)
When a `search_sentences` cursor is passed to `read_document_sentences`, don't
just reject — return a `ToolCallError(kind="bad_input", hint=…)`:

> This cursor was issued by `search_sentences`; cursors are not interchangeable
> between tools. `read_document_sentences` pages by position — pass
> `from_ordinal`/`to_ordinal`, or continue paging with its own `cursor`.

doc·ray already has the typed-error machinery (`ToolCallError` with `kind`/`hint`);
this is one more classified case. (Anthropic: actionable errors "steer agents
toward … more token-efficient strategies.")

---

## P1 — Close the reach and honesty gaps

### 4. `response_format: "concise" | "detailed"` on the readers
Add the enum to `read_document_sentences` and `search_sentences`. `concise`
returns only text + its citation handle (ordinal, `charStart`/`charEnd`);
`detailed` keeps the full metadata. Anthropic reports this pattern cut tokens by
~⅔ in their Slack tooling — directly attacking the "200 sentences ≈ 18k tokens"
ceiling that made a complete read look impossible.

### 5. `read_document_outline` — the map that aims the ranged reader (gap C)
The ranged reader is only as good as the agent's ability to guess ordinals.
Expose the document's structure — headings/sections (doc·ray already extracts
PDF layout + heading levels) with their ordinal ranges:

```jsonc
[ { "level": 1, "title": "Book I", "ordinal_start": 1, "ordinal_end": 6103 },
  { "level": 2, "title": "…", "ordinal_start": 6104, "ordinal_end": 6500 } ]
```

Now "read the ending" is `read_document_sentences(from_ordinal=<last section start>)`
— structure-addressed, not a guess. This is the missing half of the random-access
story.

### 6. Entity tools return **canonical** identities + confidence (gap B)
doc·ray *canonicalizes* entities to shared identities — but `entity_frequency`
returns raw NER, so the agent re-did that work by hand. Group by canonical
identity and surface the spread the model needs to trust it:

```jsonc
{ "canonical": "Arrakis", "kind": "place", "mentions": 812,
  "also_tagged": ["GPE", "ORG", "LOC"], "surface_forms": ["Arrakis", "Dune"],
  "confidence": 0.86 }
```

"Duncan Idaho split into a person and a state" becomes one canonical row with a
low-confidence flag, not two rows the agent must reconcile. This is
[PALS's Law](../../CLAUDE.md) applied at the surface: NER output is *presented*
as the untrusted, reconciled thing it is, with its confidence, rather than raw.

---

## P2 — Structural additions the feedback named directly

### 7. An MCP **resources** layer with URI templates (gap E)
Register resources and resource templates alongside the tools — the primitive
built for "address any slice on demand," application-controlled and RLS-scoped by
the same forwarded token:

```
docray://document/{id}                     → metadata + outline
docray://document/{id}/sentences?from=&to= → a verbatim slice
docray://collection/{id}                   → member documents
docray://lexeme/{uid}                      → KWIC occurrences
```

Resources are the clean, cacheable, host-attachable read surface tools can't be;
`resources/templates/list` is itself paginated in the spec. This is exactly the
"URI-addressed reads" the reviewer missed.

### 8. MCP **prompts** — capture the methodology (gap F)
Ship user-selectable prompts — `summarize_document`, `ground_a_claim` — that
encode the deliberate path (outline → ranged read → rare-term search → cite). A
prompt is the primitive for "the named workflow that combines the tools"
([Stacktree](https://stacktr.ee/blog/mcp-resources-vs-tools-vs-prompts)); it turns
the reviewer's hard-won method into something the next agent picks off a menu.

### 9. Namespacing so tools "divide cleanly by job" in their names
The reviewer noticed the tools divide by job; make that legible. Group by verb —
`search_*` (locate), `read_*` (verbatim), `entity_*` (structure), `collection_*`
— so the model selects the right family at a glance. (Anthropic: namespacing
"delineate[s] boundaries between lots of tools.")

### 10. A term-frequency pre-flight from the lexicon
doc·ray already stores a lexicon with per-term frequencies. Expose (or fold into
`search_sentences`' steering) a cheap "how common is this term in this document?"
so the agent can *avoid* flooding before it happens rather than diagnosing it
after — query diversification as a pre-check
([agentic retrieval](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)).

### 11. An evaluation harness
Anthropic's closing point: measure. Build a handful of grounded tasks ("summarize
this document's ending **from source**", "list every named place with a citation")
and score coverage, tokens, tool-call count, and errors. Iterate the descriptions
and return shapes above against it — that is how the SWE-bench gains were found.

---

## Priority & effort

| Item | Fixes | Effort | Status | Why first |
|---|---|---|---|---|
| 1 · server `instructions` | A,C,F | XS | **shipped 1.39.0** | One string; teaches every agent the model + tool order. |
| 2 · coverage signal | A | S | **shipped 1.39.0** | Removes the single worst failure — false "missing content". |
| 3 · cursor-error steer | D | XS | **shipped 1.39.0** | Reuses the existing typed-error path. |
| 4 · `response_format` concise | C | S | next | ~⅔ fewer tokens on the readers. |
| 5 · `read_document_outline` | C,F | M | next | Makes ranged reads aim-able. |
| 6 · canonical + confidence entities | B | M | open | Ships reconciliation the agent did by hand. |
| 7 · resources layer | E | M | open | The URI-addressed reads the reviewer wanted. |
| 8 · prompts | F | S | open | Turns the methodology into a reusable menu item. |
| 9 · namespacing | — | S | open | Legibility; a rename + description pass. |
| 10 · term-frequency pre-flight | A | S | open | Prevents flooding instead of diagnosing it. |
| 11 · eval harness | all | M | open | Closes the loop; how you tune 1–6. |

**Recommended first cut: items 1, 2, 3, 4.** *(1–3 shipped in 1.39.0 as BL-145; item 4 is the remaining piece of this cut.)* They are
mostly descriptions, return-shape tweaks, and one error branch — no new
subsystems — and they resolve the reviewer's two sharpest complaints (the false
"missing content" and the cursor dead-end) plus the token ceiling. Items 5–8 are
the substantive follow-on; the outline (5) and resources (7) are the biggest
capability adds.

## What this is not

None of this changes the core stance the reviewer praised — that doc·ray
"quietly enforces honesty: you can't assert what you didn't retrieve." Every
change above keeps citations first-class and returns *more* honest signal
(coverage, confidence, structure), not less. The goal is to move the learning
curve out of the user's session and into the server's descriptions, where it is
paid once.

## Sources

- [Anthropic — Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [MCP specification — Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) · [Pagination](https://modelcontextprotocol.io/specification/2025-03-26/server/utilities/pagination)
- [Microsoft — MCP: Tools vs Resources vs Prompts](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/mcp-demystified-tools-vs-resources-vs-prompts-explained-simply/4508057)
- [WorkOS — Understanding MCP features](https://workos.com/blog/mcp-features-guide)
- [Stacktree — MCP resources vs tools vs prompts: when to use each](https://stacktr.ee/blog/mcp-resources-vs-tools-vs-prompts)
- [Azure AI Search — Agentic retrieval overview](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)
