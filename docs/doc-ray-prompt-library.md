---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-07-31"
title: "doc-ray Corpus Prompt Library"
subtitle: "Grounded retrieval, synthesis, innovation, agentic systems, long-form writing, and creative generation"
version: "2.0.0"
status: "working library"
verified_against:
  release: "1.57.0"
  tool_registry: "src/doc_ray/entrypoints/mcp/ (35 read tools, 33 write tools, 8 resources, 4 prompts)"
  reference_doc: "docs/mcp-service.md"
  corpus_snapshot: "2026-07-31 (Appendix D)"
---

# doc-ray Corpus Prompt Library

A structured library for turning **doc-ray + any connected corpus** into grounded
answers, analyses, teaching materials, system designs, innovative composites, and
publication-ready deliverables.

The file separates reusable operating rules from task-specific prompts, and both from
reference material that should inform execution without being pasted into every request.

## Disclaimer

This work is subject to the methodological caveats and commitments described in
[@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## How this file is verified

Every capability claim below is checked against the running MCP surface rather than
remembered: the tool registries in [entrypoints/mcp/](../src/doc_ray/entrypoints/mcp/)
(`corpus.py`, `semantic.py`, `structure.py`, `mention_confidence.py`, `compose.py`,
`mutations.py`, `mutations_surface.py`), the resource and prompt registries
(`resources.py`, `prompts.py`), and the engineering reference
[docs/mcp-service.md](mcp-service.md). §3 and Appendix A are the parts that go stale
first; re-derive them from those files, not from this document, before a consequential
run.

## Contents

1. [Operating contract](#1-operating-contract)
2. [Placeholder and output conventions](#2-placeholder-and-output-conventions)
3. [doc-ray capability map](#3-doc-ray-capability-map)
4. [Retrieval integrity rules](#4-retrieval-integrity-rules)
5. [Corpus discovery and coverage](#5-corpus-discovery-and-coverage)
6. [Grounded retrieval](#6-grounded-retrieval)
7. [Cross-document synthesis](#7-cross-document-synthesis)
8. [Entities, lenses, and relationships](#8-entities-lenses-and-relationships)
9. [Teaching and knowledge validation](#9-teaching-and-knowledge-validation)
10. [Innovation and composite generation](#10-innovation-and-composite-generation)
11. [Corpus hygiene, drift, and governance](#11-corpus-hygiene-drift-and-governance)
12. [Agentic AI and software-system prompts](#12-agentic-ai-and-software-system-prompts)
13. [Long-form document generation](#13-long-form-document-generation)
14. [Creative and narrative generation](#14-creative-and-narrative-generation)
15. [Prompt-library generation](#15-prompt-library-generation)
16. [Composition workflows](#16-composition-workflows)
17. [Reusable quality gates](#17-reusable-quality-gates)
18. [Appendix A — Tool, resource, and prompt surface](#appendix-a--tool-resource-and-prompt-surface)
19. [Appendix B — Cross-corpus composite protocol](#appendix-b--cross-corpus-composite-protocol)
20. [Appendix C — Knowledge matrix](#appendix-c--knowledge-matrix)
21. [Appendix D — Corpus inventory snapshot](#appendix-d--corpus-inventory-snapshot)
22. [Appendix E — Prompt index](#appendix-e--prompt-index)

---

# 1. Operating contract

Append this contract to any prompt that must remain grounded in the connected corpus.

```text
SOURCE AUTHORITY
- Treat the visible doc-ray corpus as the only factual authority for this task.
- Do not use web sources, model memory, wikis, adaptations, secondary summaries,
  or unstated background knowledge as evidence.
- When the corpus does not support a claim, say so explicitly.

EVIDENCE
- Cite every material factual claim with a citation handle: document title or ID,
  sentence ordinal, and charStart/charEnd when the exact sub-sentence span matters.
- Read the surrounding verbatim range before asserting what a passage means.
- Distinguish direct evidence, reasonable inference, speculation, contradiction,
  and corpus silence.
- Machine output inside the corpus is evidence of a proposal, not of a fact:
  a `pending` classification, a `proposed` projection or allusion, an
  unconfirmed caption, and an unresolved entity are all untrusted until a
  human decision is recorded.

COVERAGE
- "The corpus" means the documents visible to the current access token.
- A failed search is not proof of absence, and an empty result is not the same
  as a searched-and-empty result. Before claiming absence, satisfy the
  absence protocol (P33).
- Record unembedded documents, unannotated documents, extraction failures,
  truncated or capped searches, flooded terms, stale rollups, and capabilities
  this deployment does not serve.
- Report capped counts as floors, never as totals.

EXECUTION
- Introspect the connected tool list before relying on remembered signatures.
- Prefer read-only operations. The read endpoint cannot mutate anything; a
  curation endpoint, when present, must not be used unless corpus mutation is
  explicitly requested.
- Do not silently repair, enrich, tag, delete, or reclassify corpus content.
- Curate the library; compose outside it. A generated artifact is a standalone
  deliverable and re-enters the corpus only through the normal ingest path.

OUTPUT
- Separate research/evidence from the finished user-facing artifact.
- Include a confidence-and-gaps section or source ledger when appropriate.
- Do not expose process commentary inside a publication-ready deliverable.
```

## Core status labels

| Label | Meaning |
|---|---|
| `DIRECT` | Explicitly stated in a cited corpus passage. |
| `INFERRED` | Follows from cited material but is not stated verbatim. |
| `SPECULATIVE` | Extends beyond what the cited material establishes. |
| `CONTRADICTORY` | Corpus sources rely on incompatible claims or premises. |
| `UNTESTABLE` | The visible corpus cannot currently evaluate the claim. |
| `CORPUS_SILENCE` | Retrieval found no support **and** the absence protocol (P33) was satisfied. |
| `NOT_SEARCHED` | Retrieval could not run in this scope (no vectors, unannotated, capability absent). Never report this as silence. |

`NOT_SEARCHED` exists because the two states are routinely conflated. An empty
semantic ranking, for example, cannot mean "nothing was similar" — the ranking applies
no similarity floor, so zero rows means nothing was searched (§4.3).

---

# 2. Placeholder and output conventions

Use `⟨angle-bracket placeholders⟩` for values supplied at execution time.

Common placeholders:

| Placeholder | Meaning |
|---|---|
| `⟨question⟩` | The question to answer. |
| `⟨topic⟩` | Subject to investigate. |
| `⟨claim⟩` | Proposition to test or defend. |
| `⟨entity⟩` | Person, organization, work, concept, or named object. |
| `⟨lens⟩` | A named entity-resolution scope (`list_lenses`). |
| `⟨domain⟩` | Target business, technical, scientific, or creative domain. |
| `⟨artifact⟩` | Document, system, prompt set, design, or other deliverable. |
| `⟨N⟩` | Requested number of results. |
| `⟨page target⟩` | Approximate manuscript page count. |
| `⟨audience⟩` | Intended reader, operator, stakeholder, or user group. |

Recommended prompt anatomy:

```text
ROLE
SOURCE SCOPE
OBJECTIVE
INPUTS
PROCEDURE
CONSTRAINTS
REQUIRED OUTPUT
QUALITY GATES
SOURCE LEDGER
```

---

# 3. doc-ray capability map

This map describes the surface as of release 1.57.0. It is a usage guide, not a
permanent API contract — re-introspect the connected server before execution.

## 3.1 What the server exposes

doc-ray's MCP server ([entrypoints/mcp/server.py](../src/doc_ray/entrypoints/mcp/server.py))
serves **two endpoints and three protocol primitives**:

| Surface | Path | Contents |
|---|---|---|
| Read registry | `/mcp` | 35 read tools. Cannot be widened; no write tool is ever registered here. |
| Curation registry | `/mcp/curate` | The same 35 read tools **plus** 33 write tools. Registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`; otherwise the path does not exist. |
| Resources | both | 8 `docray://` URIs — 3 concrete entry points, 5 templates. Addressing over the same read tools; no extra privilege, no smaller payload. |
| Prompts | both | 4 user-selectable workflow prompts (`summarize_document`, `ground_a_claim`, `survey_entities`, `sweep_the_corpus`). |

Enabling writes is necessary but not sufficient: the calling principal still needs
`write` scope, write-capable RBAC, and a grant on the target documents, all enforced by
the API rather than by the tool layer.

**Prefer the server's own prompts over re-deriving their methodology.** If the client
exposes MCP prompts, `summarize_document` already returns an outline-aimed coverage plan
with real ordinal ranges, and `sweep_the_corpus` already returns the map-reduce plan with
its coverage proof. The prompts in this library are for work those four do not cover.

## 3.2 Capability map by need

| Need | Preferred capability |
|---|---|
| Size the corpus before planning | `corpus_stats` (read its `guarantees`, §4.6) |
| Enumerate the visible corpus | `list_documents`, `corpus_manifest` + `corpus_shard` |
| Orient on one document | `get_document`, `read_document_outline` |
| Find documents | `full_text_search` (`mode="terms"` or `"phrase"`) |
| Pre-flight a search term | `term_frequency` (batch all candidates in one call) |
| Find exact wording | `search_sentences` (`match="substring"` or `"phrase"`), `lexeme_kwic` |
| Find passages by meaning | `semantic_search_sentences` |
| Read verbatim evidence | `read_document_sentences` (ordinal range, page, or sequential walk) |
| Retrieve bounded evidence for one document | `document_evidence` |
| Build a topic packet | `briefing_pack` |
| Profile entities | `entity_profiles` (preferred), `entity_dossier`, `entity_frequency` (raw) |
| Read identity through a lens | `list_lenses`, `entity_projection`, `lens_entities` |
| Inspect relationships | `entity_relations`, `related_entities`, `allusions` |
| Triage NER quality | `uncertain_mentions`, `entity_mention_corrections` |
| Inspect vocabulary | `term_frequency`, `lexeme_kwic` |
| Inspect grammatical/semantic structure | `document_syntax` (Layers 9 and 14) |
| Inspect genre | `document_classification` |
| Inspect collections and scopes | `list_collections`, `get_collection`, `list_lenses` |
| Work with images | `image_class_summary` → `document_images` → `visual_captions`; `cross_modal_image_search` for meaning-based image retrieval; `visual_review` for the untrusted proposal queue |
| Poll queued work | `get_job` |

## 3.3 Retrieval hierarchy

1. `corpus_stats` — size the corpus and learn which totals are exact, rollup, or debounced.
2. `full_text_search` — identify candidate documents.
3. `read_document_outline` — turn a candidate into addressable ordinal ranges.
4. `term_frequency` — pre-flight candidate search terms **before** spending a search.
5. `semantic_search_sentences` — conceptual retrieval where wording is unknown.
6. `search_sentences` / `lexeme_kwic` — exact wording, boundaries, and concordance.
7. `read_document_sentences` — read the surrounding range and quote verbatim.
8. `briefing_pack` / `document_evidence` / `entity_dossier` — candidate assembly, never final evidence; each returns `gaps` and `guidance` that must be read, not discarded.

Steps 3 and 4 are the ones agents skip and then pay for: grinding keyword searches
before mapping the document is the dominant failure mode this server was tuned against.

---

# 4. Retrieval integrity rules

These are properties of the shipped implementation, not style preferences. A prompt that
ignores them produces confident, wrong coverage claims.

## 4.1 What each search tool actually matches

| Surface | Matches on | Consequence |
|---|---|---|
| `search_sentences` (default `match="substring"`) | case-insensitive literal substring; no stemming, no lemmas, no word boundaries | `Hick` matches `thick`; `%`/`_` are data, not wildcards; typographic `’ – —  “ ”` are folded to their ASCII forms |
| `search_sentences` (`match="phrase"`) | `surface_word_v1` whole-word adjacency, with absolute per-occurrence offsets | `ngram` no longer matches `Tangram`; `algorithm design` no longer matches `algorithm designed` |
| `full_text_search` | Snowball stems via `plainto_tsquery`, per-document language config | `running` matches `run`; `mode="phrase"` additionally requires order |
| `term_frequency` (single word) | lexeme lemma (`norm_lemma`) | a saturation signal, a proxy for — not a predictor of — any one tool's hit count |
| `term_frequency` (phrase/compound) | bounded SQL candidates verified under `surface_word_v1` | `sentenceHits` are candidates; `occurrenceSentences` are verified |
| `semantic_search_sentences` | learned sentence embeddings, cosine-ranked, **no similarity floor** | zero rows means nothing was searched (§4.3) |

Source: [domain/search_terms.py](../src/doc_ray/domain/search_terms.py),
[domain/term_frequency.py](../src/doc_ray/domain/term_frequency.py),
[entrypoints/mcp/corpus.py](../src/doc_ray/entrypoints/mcp/corpus.py).

## 4.2 Flood control is a pre-flight, not a post-mortem

`search_sentences` reports flooding *after* the fact through `coverage.limit_reached` and
`note`. `term_frequency` reports it *first*, for a batch of candidate terms in one call:

- `floodRisk: high` — pick a rarer term; the hits will cluster and starve coverage.
- `attested: false` — the term is absent from the lexicon; the search will likely return nothing.
- `attested: null` — candidate fetching was capped and nothing verified; **not** an absence claim.
- `capped: true` — `sentenceHits`, `occurrenceSentences`, and `occurrences` are all **floors**.
- `indexable: false`, `floodRisk: "unknown"` — the term cannot be measured (an honest decline); use `search_sentences` directly.
- `totalSentences: null` — the maintained denominator is missing or stale; the absolute flood band still holds. Never render this as "N of 0 sentences".

`term_frequency` is corpus-wide only; passing `document_id` returns a typed `unsupported`
error rather than corpus numbers mislabelled as document-scoped.

## 4.3 An empty result is not a finding

- **Semantic search**: an empty ranking is impossible when vectors exist, because no
  similarity floor is applied. The tool therefore fails with a typed `not_found` naming
  the precondition (no vectors for the active embedding model in this scope). Embedding
  is opt-in per document (`embed_document_sentences`), so an unembedded scope is normal.
- **Keyword search**: zero hits means zero *substring* matches under the folding rules —
  it says nothing about paraphrase, and nothing about documents that are unannotated or
  whose extraction failed.
- **Entity tools**: an entity absent from `entity_profiles` may simply live in a document
  that has not been annotated.

Record these as `NOT_SEARCHED`, never as `CORPUS_SILENCE`.

## 4.4 Outline provenance decides what you may quote

`read_document_outline` returns `source`:

- `headings` — real detected structure (heuristic: markup for EPUB/DOCX/MD, relative font size for PDF). Titles are quotable.
- `uniform` — no headings found; the ranges are an even split. Equally good for aiming a ranged read, but **the titles are not chapter names** and must never be quoted as such.
- `empty` — nothing to map.

## 4.5 Paging, clamps, and cursors

- Every `limit` is served up to `MAX_PAGE_SIZE = 200` ([entrypoints/mcp/shaping.py](../src/doc_ray/entrypoints/mcp/shaping.py)). The clamp is disclosed, not silent: `search_sentences` reports it in `coverage.requested_limit` and `note`.
- Keyset paging always feeds the **last** row's `cursor` back as `after`. A non-last cursor re-serves rows you already hold.
- Cursors are per-tool and not interchangeable. `read_document_sentences` takes ordinals, not a search cursor.
- KWIC windows are clipped at 500 characters with an explicit `… [+N chars]` marker.

## 4.6 Totals carry different guarantees

`corpus_stats` returns four totals off three read paths, each with a `guarantees` entry:

- `exact` — counted live on this request (`documents`).
- `rollup` — summed from per-document rollups; exact once each document's pipeline job has committed (`sentences`, `occurrences`).
- `debounced` — recomputed at most once per `stalenessBoundSeconds`; may lag (`lexemes`).

When `stale: true`, quote the figure with its `label` ("computed 10 minutes ago") and use
`documents` — which is exact — as the denominator for any coverage fraction. The same
discipline applies to Block F rollups (`lens_entities`, `entity_relations`), which each
carry a `refreshedAt` stamp.

## 4.7 Machine output is a proposal

- `document_classification.status`: `pending` is an unverified machine guess; only `confirmed` (`method='human'`) is a decision.
- `entity_projection.status`: `proposed` until `confirm_projection`; `canonicalId: null` means explicitly unresolved, never silently dropped.
- `allusions.status`: `proposed` until `confirm_allusion`.
- `visual_captions` returns only committed, grounding-gated captions; the untrusted Tier-3 proposals live in `visual_review`.
- `entity_profiles.resolved: false` means the row is reconciled NER and nothing more — not a verified real-world identity.

## 4.8 Structural signals are not semantic claims

- `entity_relations.strength` is NPMI over co-occurrence: a structural association measure in `[-1, 1]`, not a claim that two entities are genuinely related.
- `related_entities` reachability is structural. `hops` is capped at 3 and a larger value is rejected outright, never silently clamped.
- An allusion edge is a typed link, **never an identity and never transitive**: `A→B` and `B→C` do not license `A→C` or `A≡C`.
- `entity_profiles` folds only normalization-equivalent spellings. `Duncan` and `Duncan Idaho` remain separate rows, because merging them is coreference and guessing it would be fabrication.
- `document_syntax` Layers 9 and 14 are **derived from the dependency parse**, not produced by a constituency parser or neural SRL: `frame` is the predicate lemma (`found`), never a PropBank roleset (`found.01`).

## 4.9 Typed failures and what they mean

Errors arrive as `[kind] message — hint`
([entrypoints/mcp/errors.py](../src/doc_ray/entrypoints/mcp/errors.py),
`corpus._classify_graphql_error`):

| Kind | Meaning | Correct response |
|---|---|---|
| `bad_input` | Rejected locally before any network call (malformed UUID, blank string, unknown enum). | Fix the argument. |
| `not_found` | The target does not exist, is not visible, or a precondition is unmet (e.g. no sentence vectors in scope). | Read the message: it distinguishes missing from unsearched. |
| `unsupported` | This deployment does not serve the capability (vision stack absent, document-scoped `term_frequency`). | Do not retry; use the text-side tools. |
| `timeout` | The read exceeded the API statement budget. | Narrow it — scope by `document_id`, lower `limit`, use a rarer term, or read a range directly. |
| `permission_denied` | Missing `write` scope, RBAC, or a per-document grant. | Stop; this is a decision for the operator, not a retry. |
| `upstream_error` / `upstream_unavailable` | Unclassified API failure; the transport-level variant is marked retriable. | Retry only the retriable one, then report the failure in the run ledger. |

Every degraded mode encountered belongs in the output's coverage section. Silence about a
degraded mode reads as coverage that never happened.

---

# 5. Corpus discovery and coverage

## P01 — Corpus capability audit

**Purpose:** Determine whether the corpus can support a proposed task before attempting it.

```text
Use doc-ray to determine whether the visible corpus can support this objective:
⟨objective⟩

Evaluate the corpus, not the objective in the abstract.

Procedure:
1. Size the corpus with corpus_stats and record which totals are exact, rollup,
   or debounced; enumerate collections and lenses.
2. Batch the objective's central concepts, methods, named technologies, and
   reference works through term_frequency before searching, and record which
   candidate terms are unattested, flood-risky, or unmeasurable.
3. Search with the surviving terms; use semantic_search_sentences where the
   corpus's wording is unknown.
4. For each required knowledge area, classify coverage as:
   STRONG | ADEQUATE | THIN | ABSENT | INACCESSIBLE.
5. Identify documents that provide foundational instruction, operational
   guidance, advanced or innovative ideas, counterarguments, and implementation
   evidence.
6. Record extraction, annotation, embedding, identity-resolution, and access
   gaps — including documents in status collections (annotation backlog,
   extraction failures, quarantine, unreviewed synthesis).
7. State what the corpus can teach reliably, what requires inference, and what
   it cannot currently support.

Required output:
- Executive verdict
- Requirement-to-evidence matrix
- Best source documents and passages
- Coverage gaps and their operational effect
- Recommended retrieval plan
- Minimum additional source types needed
- Confidence and limitations

Use only corpus evidence. Cite every coverage judgment. Distinguish INACCESSIBLE
(blocked by processing or access) from ABSENT (searched and not found).
```

## P02 — Prompt-engineering corpus audit

```text
Determine whether the visible doc-ray corpus contains enough material on prompt
engineering to support: ⟨intended use⟩.

Search for and distinguish coverage of:
- prompt anatomy and instruction hierarchy;
- grounding and retrieval-augmented prompting;
- scope locking and refusal behavior;
- role/persona prompting;
- few-shot examples;
- structured outputs and schemas;
- decomposition and multi-pass prompting;
- evaluation, adversarial testing, and regression prompts;
- agentic and tool-using prompts;
- prompt injection and security;
- prompt versioning, drift, and observability;
- domain-specific prompt design.

Return:
1. Coverage verdict by area
2. Corpus evidence map
3. Best learning sequence
4. Missing areas
5. Whether the corpus can support a reusable prompt library
6. Concrete next action

Do not answer from general knowledge. Cite all conclusions.
```

## P03 — Coverage and gap audit

```text
Audit how well the visible corpus covers: ⟨topic⟩.

Classify subtopics as:
- strongly covered;
- adequately covered;
- thinly covered;
- absent from visible evidence (absence protocol satisfied);
- blocked by processing or access limitations.

For thin or absent areas, identify the exact source type that would close the gap.
Cite what exists. Do not convert a failed search into a claim of absence — apply
P33 before writing "absent".
End with the three highest-leverage corpus improvements.
```

## P32 — Corpus sweep with coverage proof

**Purpose:** Cover the whole visible corpus without silently skipping documents. Use when
a conclusion is corpus-wide ("nothing in the corpus says X", "every document that does Y").

```text
Sweep the entire visible corpus for: ⟨objective⟩.

Procedure:
1. Call corpus_stats. Record `documents` (exact) as the coverage denominator, and
   note any total whose guarantee is `debounced` and `stale`.
2. Call corpus_manifest(shard_count=⟨N; default 8⟩) ONCE. Record the per-shard
   document counts and the total.
3. For each shard i in 0..N-1, page corpus_shard(shard_index=i, shard_count=N)
   with the last row's cursor until it returns []. Use the SAME shard_count for
   the manifest and every shard call; mixing values re-partitions the corpus and
   voids the proof.
4. Do the per-document work within each shard. Record, per document, whether it
   was readable, annotated, and (where relevant) embedded.
5. Coverage proof: the shards are disjoint and exhaustive, so the number of
   documents processed MUST equal the manifest's totalDocuments. If it does not,
   a shard was dropped — do not state a corpus-wide conclusion until it balances.

Required output:
- The objective's findings, each cited
- Coverage ledger: documents processed / totalDocuments, per shard
- Documents that could not contribute, with the reason (extraction failure,
  unannotated, unembedded, access)
- Degraded modes and failed calls
- The corpus-wide claims this sweep does and does not license

"The whole corpus" always means "the whole corpus this token may read." Say so.
```

---

# 6. Grounded retrieval

## P04 — Grounded question

```text
Answer this question using only the visible doc-ray corpus:
⟨question⟩

Procedure:
1. Find the most relevant documents (full_text_search).
2. Map any large document before reading it (read_document_outline).
3. Pre-flight candidate search terms in one term_frequency batch; prefer rare,
   scene-specific terms.
4. Retrieve conceptual matches (semantic_search_sentences) and exact matches
   (search_sentences; match="phrase" when boundaries or adjacency matter).
5. Read the surrounding verbatim range (read_document_sentences) before
   interpreting any hit.
6. Reconcile sources when they differ.

Rules:
- Every material claim carries a document title or ID plus ordinal.
- Separate direct evidence from inference.
- Say explicitly when the corpus does not support an answer, and distinguish
  "searched and not found" from "could not be searched".
- Do not use outside knowledge to fill gaps.

Output:
- Answer
- Evidence trace
- Alternative interpretations
- Confidence and gaps (including any coverage flags, caps, or typed failures hit)
```

## P05 — Exact wording extractor

```text
TASK
Find the corpus's exact definition or formulation of: ⟨term or proposition⟩.

PROCEDURE
- Locate candidate documents.
- Inspect their outlines; note whether `source` is headings or uniform.
- Search with match="phrase" when the formulation is multi-word, so boundaries
  and adjacency are verified rather than assumed.
- Read the complete surrounding passage.

OUTPUT
1. Verbatim sentence or bounded passage
2. Document title or ID
3. Ordinal range
4. charStart/charEnd when the sub-sentence span matters
5. One-sentence plain-language paraphrase
6. Notes on competing definitions

Do not replace the source wording with a paraphrase. If the match was found in
substring mode, say so — substring containment is not word-boundary evidence.
```

## P06 — Evidence map

```text
Build an evidence map for: ⟨question, claim, or deliverable⟩.

For each required claim or component, return:
| Requirement | Supporting document | Passage/ordinal | Evidence status |
| Interpretation | Gap or risk |

Evidence status must be one of:
DIRECT | INFERRED | SPECULATIVE | CONTRADICTORY | CORPUS_SILENCE | NOT_SEARCHED.

Do not draft the final artifact yet. The purpose of this pass is to establish
what can be responsibly claimed and where additional retrieval is required.
```

## P07 — Source-bounded answer with negative findings

```text
Answer: ⟨question⟩.

In addition to supporting evidence, actively search for:
- passages that contradict the likely answer;
- boundary cases;
- disqualifying conditions;
- exceptions;
- terms that appear similar but mean something else.

Output:
1. Supported answer
2. What not to infer
3. Exceptions and failure conditions
4. Contradictory evidence
5. Corpus silence versus unsearched scope
6. Confidence
```

## P33 — Absence claim protocol

**Purpose:** Make "the corpus does not contain X" a defensible statement. Run it before
any deliverable asserts absence.

```text
Test whether the visible corpus genuinely lacks: ⟨claim, concept, or entity⟩.

An absence claim is only defensible after ALL of the following:

1. TERM VIABILITY
   - Batch every plausible surface form through term_frequency.
   - Record attested / unattested, and note that attested:null (capped scan) and
     indexable:false (unmeasurable term) are NOT absence evidence.

2. LEXICAL SEARCH
   - Search the surviving forms with search_sentences, substring first, then
     match="phrase" where adjacency matters.
   - Record coverage.limit_reached and any note: a flooded search proves nothing
     about the regions it never reached.

3. SEMANTIC SEARCH
   - Run semantic_search_sentences for the concept, not the wording.
   - A typed not_found here means NO VECTORS EXIST IN SCOPE. That is
     NOT_SEARCHED, not absence. Name the unembedded scope explicitly.

4. STRUCTURAL SEARCH
   - Check entity_profiles / entity_dossier for the entity form.
   - Where a lens exists, check lens_entities and entity_projection.

5. SCOPE ACCOUNTING
   - List documents that could not contribute: extraction failures, unannotated
     documents, archived/unreadable documents, documents outside the token's
     visibility.
   - Express them as a fraction of corpus_stats.documents.

VERDICT — exactly one:
- CORPUS_SILENCE: all four searches ran cleanly over an accounted scope and
  found nothing.
- NOT_SEARCHED: at least one retrieval path could not run; name it.
- PRESENT: cite the evidence found.

Never emit CORPUS_SILENCE while any step reports a cap, a typed failure, or an
unaccounted scope.
```

---

# 7. Cross-document synthesis

## P08 — Steelman from source

```text
Build the strongest corpus-grounded case FOR this position:
⟨claim or position⟩

Then build the strongest corpus-grounded case AGAINST it.

Requirements:
- Cite each premise.
- Do not invent balance where one side lacks support.
- Distinguish contradiction from silence, and silence from unsearched scope.
- Identify which argument depends on fewer assumptions.
- Flag any premise drawn from a document tagged as unreviewed synthesis: it
  carries no more authority than the citations it names.
- Conclude with the decision-relevant uncertainty rather than a false synthesis.
```

## P09 — Contradiction finder

```text
Across the visible corpus, find genuine disagreements about: ⟨topic⟩.

Return:
| Issue | Source A and passage | Source B and passage | Type of conflict |
| Can both be true? | Resolution status |

Conflict types:
- factual contradiction;
- incompatible definitions;
- different scope;
- different time horizon;
- normative disagreement;
- methodological disagreement;
- apparent conflict caused by terminology;
- apparent conflict caused by edition or version drift (check for near-duplicate
  documents before recording a contradiction).

Include only tensions supported by verified passages read in context.
```

## P10 — Briefing pack to executive one-pager

```text
Assemble a doc-ray briefing pack for: ⟨topic⟩.

Read the pack's `retrieval`, `coverage`, `gaps`, and `guidance` fields before
using any passage: the pack states whether it ranked passages by learned semantic
similarity or fell back deterministically to full-text/keyword ranking, and it
names its own caps. Then verify the important passages against
read_document_sentences.

Produce a one-page brief with:
1. What the corpus establishes with high confidence
2. What remains contested
3. What is thin or missing
4. Operational implications
5. Three questions the corpus cannot currently answer
6. Source ledger, including the pack's declared retrieval mode and gaps

Do not treat briefing-pack summaries as self-validating evidence, and do not
present a capped page as a total.
```

## P11 — Concept genealogy

```text
Trace how the concept ⟨concept⟩ develops across the visible corpus.

Order the trace by conceptual dependency, not merely chronology or document order.
Use this format for each step:

→ [document:ordinal] contribution made by this passage

For every step, identify whether it:
- defines the concept;
- changes its meaning;
- introduces a mechanism;
- supplies evidence;
- adds an application;
- challenges an earlier premise;
- depends on a single source.

End with unresolved forks in the genealogy.
```

## P12 — Synthesis matrix

```text
Build a cross-document matrix for topic ⟨topic⟩ using these dimensions:
⟨dimension 1⟩, ⟨dimension 2⟩, ⟨dimension 3⟩, ⟨optional dimensions⟩.

Rows = relevant documents.
Cells = cited position, finding, method, or "not addressed."

Distinguish "not addressed" (searched within the document, absent) from
"not searchable" (document unannotated, extraction failed, or not visible).

After the matrix, identify:
- strongest convergence;
- strongest contradiction;
- shared blind spot;
- source with the broadest coverage;
- source with the deepest treatment;
- claims that remain single-source dependent.
```

---

# 8. Entities, lenses, and relationships

doc-ray resolves identity **within a lens**: a named scope under which a mention projects
to an entity. The reserved `global` lens holds real-world (Wikidata-linkable) identities;
scope lenses hold work-local identities, so a novel's character never merges with an
unrelated book's. Lenses nest through `parentLensId`. Prompts in this section should
always state which lens they read through.

## P13 — Entity dossier

```text
Build a dossier on ⟨entity⟩ using only the visible doc-ray corpus.

Start with entity_profiles (not entity_frequency): raw NER splits one entity across
types and spellings, and profiles merge them into one row with surfaceForms,
typeSpread, dominantType, and typeConsistency. Then use entity_dossier for cited
mentions plus the complete visible-document roster.

Sections:
1. Identity and type, with typeConsistency read as a measured share of mentions —
   a low value means the type is CONTESTED, not that the entity is unimportant
2. Surface forms and possible aliases (folded forms only; do not merge
   normalization-distinct forms — that is coreference, not reconciliation)
3. Every materially distinct claim, each cited
4. Documents and collections in which it appears; note that a roster document
   with cited_mention_count: 0 contains the entity but had no passage cited on
   this page
5. Relationships to other entities (see P14)
6. Contradictions or identity ambiguity
7. Unresolved references, including resolved:false rows (reconciled NER only,
   with no canonical-registry backing)

Read the dossier's `gaps` and `guidance`; a capped page is a sample, never a total.
Never merge surface forms merely because they look related. Mark unresolved
identity explicitly.
```

## P14 — Lens-scoped relationship map

```text
Within lens ⟨lens⟩, map relationships around ⟨anchor entity⟩ out to ⟨N; max 3⟩ hops.

Procedure:
1. list_lenses to resolve the lens id and its parent, if any.
2. lens_entities(lens_id) to locate the anchor's canonicalId and read its type
   profile, typeConsistency, and refreshedAt staleness stamp.
3. entity_relations(lens_id, canonical_id) for direct edges, strongest first.
4. related_entities(lens_id, canonical_id, hops=⟨N⟩) for bounded discovery.
   hops is capped at 3; a larger value is rejected outright.
5. allusions(canonical_id, lens_id, direction="both") for typed cross-lens edges.

Return edges as:
⟨A⟩ —[relationship; evidence]→ ⟨B⟩

For every edge include:
- its source: entity_relations (NPMI co-occurrence), related_entities (N-hop
  reachability), or allusions (typed, human- or machine-proposed);
- cooccurrenceCount and strength for co-occurrence edges;
- status (proposed / confirmed) for allusion edges;
- a supporting document and ordinal where one is cited;
- edge strength and ambiguity or identity risk;
- the rollup's refreshedAt stamp.

Non-negotiable readings:
- strength is NPMI: a STRUCTURAL association measure, not a claim that the two
  entities are genuinely related.
- Reachability is structural. Do not narrate a 2-hop path as a relationship.
- An allusion is an EDGE, never an identity, and never transitive: A→B and B→C
  do not license A→C or A≡C.
- A proposed edge is a machine guess awaiting human confirmation.
```

## P15 — Reading-path generator

```text
I am new to ⟨topic⟩.

Using corpus centrality, evidence quality, and conceptual dependency, identify the
five best entities, works, or concepts to study first. Prefer lens_entities where
a lens covers the topic, and entity_profiles corpus-wide otherwise.

For each item provide:
- why it matters;
- the best starting passage (document + ordinal range from read_document_outline);
- prerequisite concepts;
- what it does not cover;
- the next item in the path.

Return a sequenced learning path, not a popularity ranking. Note where the ranking
rests on a rollup with a stale refreshedAt stamp.
```

## P34 — Lens identity audit

**Purpose:** Review the projection layer's proposals for one document instead of trusting
them. Read-only unless the operator has explicitly authorized curation.

```text
Audit entity identity for document ⟨document id⟩ through lens ⟨lens; optional⟩.

Procedure:
1. document_classification(document_id) — record the genre axis and whether its
   status is a `pending` machine guess or a `confirmed` human decision. A pending
   genre must not drive an identity or merge decision.
2. entity_projection(document_id, lens_id) — read each mention's scope
   (local/global), canonicalId (null = explicitly unresolved), and status.
   Note whether `ambiguous` is true, meaning it fell back to the global lens
   because the document has several projecting lenses and no default.
3. uncertain_mentions(document_id) — the uncorrected mentions ranked
   least-trustworthy first. These are heuristic triage reasons, not calibrated
   probabilities.
4. entity_mention_corrections(document_id) — what has already been fixed, so a
   correction is not proposed twice.
5. Corroborate every contested identity with search_sentences on the surface form
   and read the surrounding range.

Required output:
| Mention (charStart–charEnd) | Surface | Proposed identity | Scope | Status |
| Evidence read | Verdict: CONFIRM / REJECT / INSUFFICIENT EVIDENCE | Rationale |

Rules:
- Propose decisions; do not enact them. confirm_projection / reject_projection /
  correct_entity_mention are curation actions requiring explicit authorization
  and the write endpoint.
- INSUFFICIENT EVIDENCE is a valid and expected verdict. A projection is derived
  and rebuildable, so leaving one unconfirmed costs nothing; confirming a wrong
  one records a human decision that is not true.
```

## P35 — Cross-lens allusion review

```text
Review or propose allusion edges connecting ⟨entity⟩ in lens ⟨lens A⟩ to entities
in lens ⟨lens B⟩.

An allusion records that one entity alludes to / depicts / references / is adapted
from / was inspired by another WITHOUT merging them. It is the honest alternative
to a lossy identity merge.

Procedure:
1. Resolve both lenses and both canonicalIds (list_lenses, lens_entities).
2. allusions(canonical_id, lens_id, direction="both") — read existing edges and
   their status.
3. For each candidate edge, retrieve the passage in the evidence document that
   licenses it and read its surrounding range.
4. Classify the edge type from the evidence, not from plausibility.

Return per edge:
| From (lens) | To (lens) | Edge type | Evidence document + ordinal |
| Verbatim support | Status | Verdict: PROPOSE / CONFIRM / REJECT / INSUFFICIENT |

Rules:
- Never present an edge as identity, and never chain edges transitively.
- Reading an edge requires visibility of BOTH lenses and the evidence document;
  say so when a candidate cannot be evaluated for that reason.
- Proposing or confirming an edge is a curation action requiring the write
  endpoint and explicit authorization.
```

## P37 — Structural evidence extraction

**Purpose:** Answer "who did what to whom", or which phrase a modifier attaches to, from
the annotation graph rather than from a paraphrase of the sentence.

```text
For ⟨document id⟩, resolve this structural question: ⟨question about grammatical
or semantic structure⟩.

Procedure:
1. Locate the relevant sentences first (search_sentences / read_document_sentences).
   document_syntax explains the shape of text you have already located; it is not
   a finding tool.
2. document_syntax(document_id) — read constituents (Layer 9) outermost-first,
   rebuilding the tree by linking each node to its parentId (null at each sentence
   root), and srlFrames (Layer 14) with their inline arguments.
3. Quote the exact span for every structural claim: each node and argument carries
   charStart/charEnd and is citable exactly like a sentence hit.

Required output:
| Claim | Constituent or frame | Role (ARG0/ARG1/ARGM-*) | Span (charStart–charEnd) |
| Verbatim text | Confidence |

Non-negotiable readings:
- Both layers are DERIVED from the dependency parse, not produced by a
  constituency parser or a neural SRL model.
- `frame` is the predicate LEMMA (e.g. `found`), never a PropBank roleset
  (`found.01`). Do not assert a sense distinction the pipeline never determined.
- Labels are limited to S, NP, VP, PP, ADJP, ADVP, SBAR; a non-projective phrase
  is recorded by its covering span.
```

---

# 9. Teaching and knowledge validation

## P16 — Teach from corpus

```text
Act as a patient tutor whose factual authority is limited to the visible doc-ray
corpus.

Teach ⟨topic⟩ at three escalating levels:
1. Intuition
2. Mechanism
3. Edge cases, limits, and failure modes

For each level:
- cite every factual claim;
- include one worked example supported by the corpus;
- explain what not to do or infer;
- end with one diagnostic question.

When the corpus cannot support a level, state the missing knowledge instead of
filling it from general memory.
```

## P17 — Ten-cell knowledge cartridge

```text
Create a compact but complete knowledge cartridge for: ⟨practice or capability⟩.

Use the following 5×2 matrix:

| Dimension | FAZER | NÃO FAZER |
|---|---|---|
| O QUE | What constitutes the practice | Confusable actions that are not the practice |
| POR QUÊ | Justification and mechanism | Costs, trade-offs, and invalid reasons |
| QUANDO | Enabling conditions | Disqualifying conditions |
| COMO | Correct execution | Errors and anti-patterns |
| CONSEQUÊNCIAS | Effects of doing it | Effects of not doing it |

Required additions:
- É / NÃO É polarity for propositional knowledge when relevant;
- evidence citation in every cell;
- uncertainty and corpus-silence markers;
- one transfer exercise;
- one adversarial counterexample;
- one teach-back test;
- correction procedure and persistence boundary.

A cartridge is incomplete if the negative column is thin or unsupported.
```

## P18 — Teach-back validation

```text
Evaluate whether ⟨person, agent, document, or model output⟩ demonstrates actual
knowledge of ⟨subject⟩ rather than affirmative fluency.

Test:
1. Can it state what to do?
2. Can it distinguish what resembles the practice but is not it?
3. Can it explain why and why not?
4. Can it identify enabling and disqualifying conditions?
5. Can it predict consequences of action and inaction?
6. Can it detect its own failure mode?
7. Can it revise the model and preserve the revision across a persistence boundary?
8. Can it transfer to an adversarial case not explicitly present in the teaching set?

Return a profile vector rather than one ordinal score.
Separate reported consequences from predicted-and-observed consequences.
```

---

# 10. Innovation and composite generation

## P19 — Corpus-grounded ideation

```text
Generate ⟨N⟩ novel applications or combinations for ⟨domain⟩ that follow from
ideas actually present in the visible corpus.

For each idea provide:
1. The two or more cited corpus premises it depends on
2. The new composite claim
3. Why neither premise alone yields the result
4. What is genuinely new versus already stated
5. Strongest objection
6. Defeater search performed, with the queries and hit counts
7. Status: SUPPORTED | SPECULATIVE | CONTRADICTORY | UNTESTABLE
8. What the idea enables
9. Concrete validation experiment

Reject ideas that rest only on metaphorical similarity or a single source.
Generate widely and keep narrowly. Report candidate and survivor counts.
```

## P20 — Cross-modal concept board

```text
For ⟨concept⟩, assemble a corpus-grounded concept board.

Procedure:
1. image_class_summary(document_id) on candidate documents — a cheap check of
   whether the visual layer is worth querying at all.
2. cross_modal_image_search(query) for meaning-based image retrieval. This
   requires the vision stack on the API host; a typed `unsupported` error means
   the deployment does not serve it — do not retry, and report the board as
   text-only.
3. document_images(document_id) for the deterministic Tier-1 features (page,
   source, class, saturation/contrast/edge density, dominant palette).
4. visual_captions(document_id) for COMMITTED, grounding-gated captions only.
   Untrusted Tier-3 proposals are not returned here; they live in visual_review
   and must not be quoted as descriptions of an image.

Return the ⟨N⟩ most relevant images, each paired with:
- document and page;
- image relevance explanation;
- supporting text passage and ordinal;
- caption provenance (committed caption, deterministic features only, or none);
- confidence;
- uncertainty or mismatch warning.

The text evidence must explain the concept; the image must add visual value rather
than merely sharing keywords or a similarity score.
```

## P21 — Cross-corpus composite generator

```text
You operate over a visible corpus exposed through doc-ray. Your task is generative,
not merely retrieval: discover pairs or triples of ideas the corpus does not
currently connect, then test whether a load-bearing connection exists.

FRAME
- Establish visible corpus coverage (corpus_stats + corpus_manifest), collections,
  lenses, and degraded modes.
- Define an operational proxy for "different domains" and state it.

SELECT
- Generate at least ⟨candidate budget; default 40⟩ candidate pairs.
- Prefer unrelated subjects sharing a mechanism, failure mode, dependency, or
  transformation pattern.
- Reject pairs already connected by: document co-occurrence; shared collection or
  lens; an existing entity_relations edge or related_entities path within a lens;
  an existing allusion edge; known surface-form variants; or a semantic probe
  naming both ideas.

GROUND
- Retrieve and read verbatim passages for both sources.
- Confirm the structural similarity survives substitution of the raw passages.
- Discard pairs whose resemblance exists only in your paraphrase.

COMPOSE
Write:
P_A — cited premise from source A
P_B — cited premise from source B
∴ C — composite claim

Delete each premise in turn. If C still follows, the merge is decorative; discard it.

FALSIFY
- Name the strongest defeater.
- Search the corpus for the defeater, lexically and semantically.
- Report queries and hit counts.
- Treat no hit as corpus silence ONLY if P33 is satisfied; otherwise report
  NOT_SEARCHED.
- Preserve contradictions rather than smoothing them over.

LOCATE
State what C predicts, forbids, or permits that was unavailable before, and define
an executable test.

OUTPUT PER SURVIVOR
COMPOSITE
SOURCE A
SOURCE B
NECESSITY
OBJECTION
DEFEATER SEARCH
STATUS
ENABLES
TEST

RUN LEDGER
- corpus size and coverage fraction (against corpus_stats.documents, which is exact);
- candidates generated;
- discards by phase;
- survivor count;
- degraded modes;
- failed tool calls and their typed kinds.
```

---

# 11. Corpus hygiene, drift, and governance

## P22 — Duplicate and identity-drift check

```text
Inspect the corpus for likely duplication, version drift, and identity splitting
around: ⟨topic or collection⟩.

Find:
- duplicate or near-duplicate documents;
- obsolete versus current versions (note that a deliberately retained edition pair
  is not a duplicate — check the collection's stated policy first);
- conflicting metadata;
- the same entity under different spellings or names (entity_profiles surfaceForms);
- different entities incorrectly collapsed;
- entities whose typeConsistency indicates a contested type;
- claims whose citation target changed across versions.

Return:
| Finding | Evidence | Risk | Recommended canonical choice | Human decision required |

Do not mutate the corpus. Produce a review queue and decision rationale.
```

## P36 — Visual caption trust review

```text
Review the untrusted visual-caption queue for ⟨document id; optional⟩.

Procedure:
1. visual_review(document_id, status="pending") — the Tier-3 proposals awaiting a
   human trust decision, with their groundingScore and groundingEvidence.
2. For each proposal, read the corresponding row in document_images (page, class,
   deterministic features) and the surrounding text via read_document_sentences.
3. Judge whether the proposed caption describes THIS image, using the page context
   and the deterministic features — not the caption's own fluency.

Return per proposal:
| Caption id | Page | Proposed caption | Grounding score | Page-context evidence |
| Verdict: APPROVE / REJECT / INSUFFICIENT EVIDENCE | Rationale |

Rules:
- A caption in this queue is untrusted VLM output. Never quote one as a
  description of the image while it is pending.
- decide_visual_captions and retract_visual_caption are curation actions requiring
  the write endpoint and explicit authorization. Propose; do not enact.
- A published caption that is wrong about its image is a retraction case, not an
  edit case.
```

## P23 — Corpus drift monitor specification

```text
Design a repeatable process that detects when the corpus, its derived artifacts,
or an AI system grounded in it has drifted.

The process must cover:
- corpus membership changes;
- document-version changes;
- extraction and annotation changes;
- embedding-model or retrieval changes (including vectors made invisible by a
  model cutover);
- entity-resolution, projection, and allusion changes;
- rollup staleness (corpus_stats guarantees, Block F refreshedAt stamps);
- prompt and tool-signature changes;
- output-schema changes;
- citation breakage (ordinals and char offsets shifting under re-extraction);
- behavior regressions;
- human policy decisions.

For every detected change, define:
1. What changed
2. Why it changed
3. Evidence and before/after diff
4. Affected claims, prompts, agents, and deliverables
5. Required regression tests
6. Human approval threshold
7. Rollback or migration procedure
8. Durable decision record

Produce a formal operating model, event schema, review workflow, and dashboard
requirements. Ground design decisions in corpus evidence.
```

## P24 — Decision provenance and human-awareness system

```text
Using doc-ray as the sole research authority, design a decision-provenance system
for ⟨software product or codebase⟩ that prevents unexplained AI-generated drift and
keeps humans aware of structural consequences.

Use the corpus to identify fundamentals and advanced ideas from:
- software and systems engineering;
- MCP and tool architecture;
- developer experience;
- computer graphics or visualization;
- formal methods and knowledge representation;
- human factors and governance.

The design must make visible:
- the decision;
- alternatives considered;
- evidence used;
- assumptions;
- constraints;
- affected architecture and code regions;
- short- and long-term consequences;
- reversibility;
- responsible human;
- agent actions;
- tests and invariants;
- drift indicators.

Required deliverables:
1. Failure-mode taxonomy for "AI-sloped" codebases
2. Decision-record schema
3. Architecture and dependency map
4. Change-impact graph
5. Human review and escalation workflow
6. Agent guardrails and permission model
7. Regression and invariant strategy
8. Visual dashboard or atlas specification
9. Adoption plan
10. Source ledger

Do not produce generic best practices. Every material design choice must be tied
to corpus evidence or marked as an invention requiring validation.
```

---

# 12. Agentic AI and software-system prompts

## P25 — Corpus-to-Agentic-AI feasibility study

```text
Use doc-ray to determine whether the visible corpus can teach and guide us in
turning the corpus itself into an Agentic AI capability.

Target capability:
- agents can retrieve and verify corpus evidence;
- agents can complete bounded tasks;
- agents can define and execute A2A pipelines;
- agents can expose reusable capabilities as an agentic library;
- all decisions and outputs remain traceable to sources and policies.

Assess corpus support for:
1. Agent architecture
2. MCP and tool contracts
3. Planning and task decomposition
4. Retrieval and grounding
5. Memory and state
6. A2A communication
7. Workflow or pipeline definition
8. Tool permissions and sandboxing
9. Validation and quality gates
10. Observability and provenance
11. Human oversight
12. Security and prompt injection
13. Evaluation and regression testing
14. Library/API/SDK design

Output:
- Overall feasibility verdict
- Coverage matrix
- Foundational sources
- Innovative sources or combinations
- Missing knowledge
- Risks caused by corpus limitations
- Recommended staged architecture
- Minimum viable proof of concept
- Source ledger
```

## P26 — Agentic library and self-defined A2A pipelines

```text
Based only on evidence retrieved from doc-ray, design an Agentic AI library that
allows agents to complete tasks and operate self-defined A2A pipelines.

The design must define:
- agent identity, capability, role, and lifecycle;
- task, plan, step, dependency, artifact, and result models;
- capability discovery and negotiation;
- typed messages and protocol envelopes;
- pipeline creation, validation, execution, suspension, and recovery;
- MCP tool binding and permission boundaries, including a read/write endpoint
  split and the difference between advisory tool annotations and authoritative
  enforcement;
- evidence retrieval and citation propagation;
- memory scopes and persistence;
- policy precedence;
- human approval points;
- observability, replay, and audit;
- failure, timeout, retry, compensation, and idempotency semantics, keyed to
  typed failure kinds rather than string matching;
- testing strategy;
- extension and plugin model.

Required output:
1. Corpus evidence map
2. Architecture principles
3. Domain model
4. Component architecture
5. Protocol and message schemas
6. Pipeline DSL or declarative schema
7. Runtime execution model
8. Security and governance model
9. Example A2A workflows
10. API/SDK/MCP surfaces
11. Test and conformance suite
12. Implementation roadmap
13. Explicit inventions versus corpus-derived decisions
14. Source ledger

Do not claim that the corpus supports a feature unless a verified passage does.
```

## P27 — Corpus-grounded architecture oracle

```text
Use the doc-ray corpus as an architecture oracle for: ⟨system or problem⟩.

Search across these subject areas when the corpus covers them:
- MCP and tool architecture;
- software and systems engineering;
- developer experience;
- computer graphics and visualization;
- formal methods;
- agentic AI;
- security and governance;
- product and human factors.

For each design problem:
1. Retrieve foundational principles.
2. Retrieve recent or unconventional approaches within the corpus.
3. Identify contradictions and trade-offs.
4. Convert evidence into candidate design decisions.
5. Falsify each decision.
6. Identify human judgment points.
7. Define architecture invariants and regression tests.

Return a decision-oriented architecture document with citations, not a literature
summary.
```

---

# 13. Long-form document generation

Composed documents are standalone deliverables. They are never written back into the
corpus by these prompts; if a composition should become a source, it re-enters through
the normal ingest path like any other document.

## P28 — Corpus-grounded monograph

```text
Create a detailed, coherent, publication-ready document based solely on the visible
doc-ray corpus.

SUBJECT
⟨subject⟩

TARGET
- Approximate length: ⟨page target; default 32 pages⟩
- Estimated words per page: ⟨250–300 unless otherwise specified⟩
- Audience: ⟨audience⟩
- Purpose: ⟨purpose⟩

RESEARCH PASS — COMPLETE BEFORE DRAFTING
1. Audit corpus coverage (P01).
2. Build an evidence map (P06).
3. Identify contradictions, thin areas, and source dependencies.
4. Define the central thesis and chapter logic.
5. Create a word-count budget by section.
6. Distinguish corpus facts, synthesis, and original framework choices.

DOCUMENT REQUIREMENTS
- coherent argument rather than stitched summaries;
- minimal repetition;
- definitions before dependent concepts;
- examples tied to evidence;
- explicit limits and counterarguments;
- practical implications;
- citations for factual claims;
- source commentary kept out of the main prose where it would disrupt reading.

REQUIRED PRELIMINARY OUTPUT
1. Coverage verdict
2. Evidence map
3. Thesis and document architecture
4. Section-level word budget
5. Risks and mitigation decisions

REQUIRED FINAL OUTPUT
1. Title
2. Executive summary
3. Main document
4. Conclusions and recommendations
5. Canon/fact versus synthesis notes
6. doc-ray source ledger

QUALITY GATES
- target length is met without padding;
- every section advances the argument;
- no unsupported factual authority;
- contradictions are not hidden;
- invented frameworks are labeled;
- terminology remains consistent;
- citations are traceable to document + ordinal;
- the result reads as a finished document, not a research log, and contains no
  commentary about the tools or pipeline used to produce it.
```

## P29 — Knowledge cartridge document

```text
Using only doc-ray evidence, compose a condensed but complete knowledge cartridge
for: ⟨subject or capability⟩.

The cartridge must serve both human practitioners and Agentic AI systems.
It must be independently understandable, operational, testable, and traceable.

Scale its length according to corpus size and complexity. Use the larger of:
- a defined minimum page count; or
- a defined percentage of relevant corpus pages.

Required architecture:
1. Scope and authority
2. Core concepts and vocabulary
3. Knowledge-dependency graph
4. O QUE / POR QUÊ / QUANDO / COMO / CONSEQUÊNCIAS matrix
5. Positive and negative examples
6. Procedures and decision rules
7. Failure modes and anti-patterns
8. Consequence and trade-off map
9. Human workflow
10. Agent workflow
11. Detailed process diagrams or flowcharts
12. Validation exercises
13. Skill-craft certificate criteria
14. Maintenance, drift, and revision procedure
15. Evidence ledger

Distinguish:
- corpus-derived requirements;
- deterministic derivations;
- editorial synthesis;
- unsupported or proposed extensions.
```

---

# 14. Creative and narrative generation

## P30 — Corpus-grounded three-chapter Dune fan-fiction novel

> This prompt preserves the requested universe and continuity constraints while
> requiring original prose rather than direct imitation of a living or deceased
> author's distinctive style.

```text
TASK
Write a three-chapter, approximately 73-page fan-fiction novel set within the
established Dune universe.

CORE PREMISE
Paul Atreides receives a message from a parallel dimension. The message claims
that he can ally with thinking machines, travel into the distant past, and rewrite
approximately ten thousand years of human history. This may prevent catastrophes
he has foreseen, but it may also erase the civilizations, religions, bloodlines,
identities, and people produced by that history.

CENTRAL QUESTION
Can Paul liberate humanity from the deterministic forces of history without
destroying the humanity he intends to save?

SOURCE CONSTRAINT
Use only the visible doc-ray corpus as the research and continuity authority.
Do not use web sources, model memory, adaptations, wikis, or secondary summaries.

CONTINUITY PASS — BEFORE DRAFTING
Work through the corpus's lens hierarchy: the canonical works and the expanded
universe are separate scopes, and a claim from one is not a claim from the other.
Establish which lens each continuity fact comes from and say so.

Identify and cite canonical material concerning:
- Paul Atreides;
- prescience and its limits;
- the Golden Path;
- the Butlerian Jihad;
- thinking machines;
- ancestral and genetic memory;
- breeding programs;
- time, causality, religion, empire, and historical recurrence.

Build a continuity map and distinguish:
- canonical facts (with lens attribution);
- reasonable extrapolations;
- inventions created for this story.

Do not contradict corpus-supported lore unless the contradiction is an intentional
consequence of the parallel dimension or altered timeline.

NARRATIVE POSITION
Choose the canonical moment in Paul's life that creates the greatest dramatic and
philosophical pressure. Before the manuscript, explain the placement in no more
than 150 words.

LITERARY DIRECTION
Do not imitate Frank Herbert's prose, reproduce canonical wording, or recycle
recognizable dialogue. Write original philosophical, politically intricate,
psychologically intense epic science fiction characterized by:
- strategic and concealed intention;
- tension between private consciousness and public myth;
- interacting ecological, religious, political, and historical systems;
- multilayered dialogue;
- restrained but forceful imagery;
- interior reflection integrated with action;
- chapter epigraphs;
- irreversible moral costs;
- prescience as both power and imprisonment;
- institutions and beliefs as forces acting on people;
- minimal exposition detached from immediate conflict.

MESSAGE REQUIREMENTS
The message's origin, reliability, motives, and ontology must remain contested.
Explore at least four interpretations:
1. a genuinely parallel Paul;
2. a machine intelligence manipulating Paul;
3. an inaccessible branch of prescience;
4. a trap generated by Paul's desire to escape his own future.

MACHINE REQUIREMENTS
The machines must not be uniformly evil, benevolent, or emotionally human.
Give them a coherent nonhuman account of freedom, suffering, continuity, memory,
prediction, and human self-deception.

TEMPORAL RULES
Establish explicit rules for:
- what can change;
- replacement, branching, or coexistence of timelines;
- memory survival;
- prescience's limits;
- the price of crossing or modifying history;
- why the method cannot be repeated without consequence.

The temporal mechanism must emerge from corpus-supported concepts rather than
arbitrary technology.

CHARACTERS
Paul must remain brilliant, dangerous, compassionate, compromised, and divided.
His conflict must include the possibility that:
- prescience helped create his prison;
- hatred of machines rests partly on myth;
- the machine solution destroys meaningful freedom;
- altered history erases Chani, his children, the Fremen, or Paul;
- refusal condemns humanity to preventable disaster.

Use at least three major canonical relationships whose emotional and political
consequences materially affect the decision. Every original character or
intelligence must have an objective, leverage, limitation, and a reason Paul
cannot simply command or dismiss it.

THREE-CHAPTER ARCHITECTURE

Chapter I — The Message — 22–24 pages
- establish the canonical situation and immediate conflict;
- introduce the impossible message;
- provide reasons to believe and distrust it;
- reveal the machine alliance;
- end with an irreversible threshold crossing.

Chapter II — The Machine Path — 25–27 pages
- bring Paul into direct contact with the machine perspective;
- establish temporal rules and cost;
- reveal conflicting accounts of the Butlerian Jihad;
- show at least two alternate histories, each better in one respect and horrifying
  in another;
- require a change to humanity's relationship with memory, authority, or prediction;
- end with the true personal cost.

Chapter III — Ten Thousand Years Rewritten — 22–24 pages
- force a choice among preservation, replacement, branching, sabotage, or a new
  alternative;
- make the climax depend on accumulated rules, character, and philosophy;
- resolve the immediate decision while preserving ambiguity about prescience;
- show intimate and civilizational consequences;
- end by recontextualizing the opening message.

LENGTH AND PACING
- Total target: approximately 73 manuscript pages.
- Target word count: approximately 18,250–21,900 words at 250–300 words per page.
- Do not pad with repeated reflection, lore summaries, redundant description, or
  dialogue that restates known information.
- Every scene must perform at least two functions: advance plot, change a
  relationship, reveal strategy, complicate philosophy, establish a temporal rule,
  or force an irreversible choice.

PRELIMINARY OUTPUT
1. Continuity placement
2. Corpus evidence map, with lens attribution per fact
3. Canonical facts versus inventions
4. Parallel-dimension and temporal rules
5. Character and faction objective matrix
6. Scene outline
7. Word-count budget by scene
8. Continuity risks and mitigation

FINAL OUTPUT
1. Title
2. One-sentence premise
3. Continuity note
4. Dramatis personae
5. Chapter I
6. Chapter II
7. Chapter III
8. Canon and invention notes
9. doc-ray source ledger

QUALITY GATES
- total length is within the target range;
- each chapter has a complete dramatic arc;
- Paul's decision follows from established character and accumulated evidence;
- the machine philosophy is coherent and nonhuman;
- temporal rules remain consistent;
- no solution appears without prior establishment;
- historical alterations create losses as well as gains;
- canonical characters retain recognizable motives and constraints;
- corpus-derived claims are traceable, with lens attribution;
- invented material is clearly labeled;
- prose is original;
- the action resolves without eliminating philosophical ambiguity.

Deliver a finished manuscript, not a synopsis, treatment, or sample chapter.
```

---

# 15. Prompt-library generation

## P31 — Generate a reusable prompt set from the corpus

```text
Based solely on the visible doc-ray corpus, identify the strongest prompting
lessons it contains and use them to create a reusable prompt library that works
with doc-ray and any domain corpus.

RESEARCH
- Find corpus evidence for prompting methods such as direct instruction,
  input/output prefixes, structured output, examples, personas, grounding,
  decomposition, iteration, evaluation, and scaling.
- Identify contradictions, missing methods, and outdated recommendations.

PROMPT-LIBRARY REQUIREMENTS
Organize prompts by function:
1. Retrieval and grounded answers
2. Evidence extraction
3. Cross-document synthesis
4. Entities, lenses, and relationships
5. Teaching and onboarding
6. Decision support
7. Adversarial review
8. Innovation and cross-domain combinations
9. Corpus hygiene and drift
10. Long-form document production
11. Agentic AI and workflows
12. Visual and cross-modal work

For every prompt provide:
- ID and name;
- purpose;
- when to use it;
- required inputs;
- doc-ray tools, with the signatures verified against the live registry rather
  than remembered;
- prompt body;
- expected output;
- failure modes;
- quality gates;
- corpus citations supporting the prompt design.

Include:
- a global guardrail footer;
- composition patterns for chaining prompts;
- a tool cheat sheet with its verification date;
- a versioning and maintenance procedure;
- explicit gaps the corpus could not support.

The result must generate concrete value, not merely retrieve or summarize content,
and must not describe the tooling used to produce it.
```

---

# 16. Composition workflows

## Deep research

`P01 Capability Audit → P04 Grounded Question → P09 Contradiction Finder → P03 Coverage Audit`

Use when the objective is to answer a difficult question while making uncertainty visible.

## Corpus-wide claim

`P01 Capability Audit → P32 Sweep with Coverage Proof → P33 Absence Protocol → P06 Evidence Map`

Use when the conclusion quantifies over the whole corpus ("no document says…", "every…").

## New-domain onboarding

`P15 Reading Path → P16 Teach from Corpus → P11 Concept Genealogy → P17 Knowledge Cartridge`

Use when a human or agent must acquire operational competence rather than receive a summary.

## Decision support

`P10 Executive Brief → P08 Steelman → P07 Negative Findings → P24 Decision Provenance`

Use when a consequential decision must remain reviewable and reversible.

## Innovation sprint

`P03 Coverage Audit → P19 Grounded Ideation → P21 Composite Generator → P12 Synthesis Matrix`

Use when looking for new value without accepting unsupported analogy.

## Entity fabric review

`P13 Entity Dossier → P34 Lens Identity Audit → P14 Relationship Map → P35 Allusion Review`

Use when identity, not text, is the object of study — and when proposals must be separated
from decisions.

## Agentic-system design

`P25 Feasibility → P27 Architecture Oracle → P26 Agentic Library → P23 Drift Monitor`

Use when converting corpus knowledge into an operational agent system.

## Publication workflow

`P01 Capability Audit → P06 Evidence Map → P28 Monograph → Reusable Quality Gates`

Use for long-form, source-grounded documents.

## Creative continuity workflow

`P01 Capability Audit → P06 Evidence Map → P30 Dune Novel Prompt → Continuity Quality Gates`

Use when creative invention must remain distinguishable from canon.

---

# 17. Reusable quality gates

Apply the relevant gates before accepting any output.

## Evidence gates

- Every material factual claim has a valid citation handle (document + ordinal, plus offsets where the span matters).
- The cited passage was read in context, not quoted from a search window.
- Direct evidence is distinguishable from inference.
- Corpus silence is not presented as proof of absence, and unsearched scope is not presented as silence.
- Contradictory sources remain visible.
- Access and processing limitations are recorded.

## Retrieval integrity gates

- Search terms were pre-flighted; flooded or unattested terms are reported, not silently dropped.
- Capped or truncated results are reported as floors, never as totals.
- `coverage.limit_reached`, `note`, `gaps`, and `guidance` fields were read and reflected in the output.
- Outline provenance is respected: `uniform` grid titles are never quoted as chapter names.
- Stale rollups are quoted with their staleness label; coverage fractions use the exact `documents` total.
- Every typed failure encountered (`unsupported`, `timeout`, `not_found`, `permission_denied`) appears in the coverage section.

## Identity and relationship gates

- Lens scope is stated for every identity claim.
- `proposed` / `pending` machine output is never reported as decided.
- NPMI strength and N-hop reachability are described as structural, not semantic.
- Allusion edges are never presented as identity and never chained transitively.
- Surface forms are not merged beyond normalization equivalence.

## Structural gates

- The output follows the requested schema.
- Definitions precede dependent claims.
- Terminology is consistent.
- Repeated sections have been removed.
- Every section contributes to the objective.
- The final artifact is separated from research commentary, and contains no description of the tooling or process used to produce it.

## Innovation gates

- Each composite depends on at least two sources.
- Removing either source breaks the inference.
- Similarity is mechanistic, not merely metaphorical.
- The strongest defeater was searched, with queries and hit counts reported.
- Unsupported novelty is labeled speculative.
- A concrete validation test is supplied.

## Agentic-system gates

- Tool permissions are explicit, and the read/write boundary is enforced by the endpoint, not by convention.
- Human approval boundaries are explicit.
- State, memory, and persistence semantics are explicit.
- Retries, idempotency, compensation, and recovery are keyed to typed failure kinds.
- Evidence and citation provenance survive across agents.
- Agent-generated changes have regression tests and rollback paths.
- Architecture decisions have durable rationale.

## Long-form writing gates

- Word/page target is met without padding.
- Every chapter or section has a complete function.
- Claims remain traceable.
- Invented frameworks or fiction are labeled.
- The prose does not imitate a specific author's distinctive wording.
- The ending or conclusion resolves the declared objective.

---

# Appendix A — Tool, resource, and prompt surface

> Derived from the tool registries in
> [src/doc_ray/entrypoints/mcp/](../src/doc_ray/entrypoints/mcp/) at release 1.57.0
> (2026-07-31). Contract tests keep the registries, the metadata catalog, and
> [docs/mcp-service.md](mcp-service.md) in agreement, but a connected deployment may
> run a different release — re-introspect before a consequential run.

## A.1 Read tools (35) — always registered at `/mcp`

**Corpus and orientation**

```text
corpus_stats()
corpus_manifest(shard_count)
corpus_shard(shard_index, shard_count, limit=50, after=None)
list_documents(limit=25, doc_type=None, status=None, after=None)
get_document(document_id)
get_job(job_id)
list_collections(limit=50, kind=None, after=None)
get_collection(collection_id, document_limit=50)
list_lenses(limit=50, after=None)
```

**Search and reading**

```text
full_text_search(query, limit=25, mode="terms")            # or mode="phrase"
search_sentences(term, document_id=None, limit=20, after=None,
                 match="substring", response_format="detailed")   # or match="phrase"
semantic_search_sentences(query, document_id=None, limit=25)
read_document_outline(document_id, max_entries=50)
read_document_sentences(document_id, limit=200, after=None,
                        from_ordinal=None, to_ordinal=None, page=None,
                        response_format="detailed")
lexeme_kwic(uid, document_id=None, limit=40)
term_frequency(terms, document_id=None)                    # corpus-wide only
```

**Entities, lenses, and structure**

```text
entity_profiles(document_id=None, entity_type=None, limit=50)
entity_frequency(document_id=None, entity_type=None, limit=50)
entity_mention_corrections(document_id)
uncertain_mentions(document_id, limit=100, entity_type=None, after=None)
document_classification(document_id)
document_syntax(document_id, limit=50, after=None)
entity_projection(document_id, lens_id=None, limit=100, after=None)
lens_entities(lens_id, limit=100, after=None, min_mentions=0)
entity_relations(lens_id, canonical_id, limit=50, min_strength=0.0, after=None)
related_entities(lens_id, canonical_id, hops=2, limit=50)   # hops capped at 3
allusions(canonical_id, lens_id, direction="both", limit=50, after=None)
```

**Visual layer**

```text
document_images(document_id, limit=100, after=None)
image_class_summary(document_id)
cross_modal_image_search(query, limit=25)                  # typed unsupported without the vision stack
visual_captions(document_id, limit=100)                    # committed captions only
visual_review(document_id=None, status="pending", limit=100, after=None)
```

**Composition grounding**

```text
briefing_pack(topic, max_documents=6, passages_per_document=4, max_entities=25)
entity_dossier(name, max_mentions=40)
document_evidence(document_id, focus, max_passages=12)
```

Every `limit` is served up to 200. Composition tools return `coverage`, `gaps`, and
`guidance`; read them.

## A.2 Write tools (33) — opt-in, served only at `/mcp/curate`

Registered only when the deployment sets `DOCRAY_MCP_ENABLE_WRITE`. Each still requires
`write` scope, write-capable RBAC, and a per-document grant; all enqueue reversible jobs
or record curation decisions. No document delete/archive, backup/restore, or job-cancel
verb is exposed; `delete_collection` removes only the container and never touches its
member documents.

```text
# Reprocessing (each returns a Job — poll with get_job)
ingest_document(ref, title=None, pipeline=True)
reannotate(document_id, lang="en")
reextract_document(document_id)
annotate_document(document_id, lang="en")
semantic_enrich_document(document_id)
embed_document_sentences(document_id)
canonicalize_document(document_id, resolve=False)

# Suggestion and decision
suggest_seeds(document_id, limit=40)
suggest_document_metadata(document_id)
decide_seeds(approve_ids=None, reject_ids=None)
decide_document_metadata_suggestions(approve_ids=None, reject_ids=None)
seed_approved(document_id)

# Metadata and NER curation
update_document_metadata(document_id, title=None, description=None, tags=None, authors=None)
bulk_tag_documents(document_ids, tags)
correct_entity_mention(document_id, action, char_start, char_end, entity_type=None,
                       surface=None, new_char_start=None, new_char_end=None,
                       original_type=None, rationale=None)
correct_entity_surface(surface, original_type, action, document_id=None,
                       collection_id=None, entity_type=None, rationale=None,
                       dry_run=True)

# Classification, projection, allusion (SLP Blocks C–E)
classify_document(document_id)
confirm_document_classification(document_id, genre, subtype=None, rationale=None)
confirm_projection(mention_id, lens_id)
reject_projection(mention_id, lens_id)
propose_allusion(from_canonical_id, from_lens_id, to_canonical_id, to_lens_id,
                 edge_type, evidence_document_id, confidence=None, rationale=None)
confirm_allusion(allusion_id)
reject_allusion(allusion_id)

# Visual review
decide_visual_captions(approve_ids=None, reject_ids=None)
retract_visual_caption(caption_id, reason)

# Collections and lenses
create_collection(name, description="", kind="manual", rule=None, doc_type=None,
                  status=None, tags=None)
add_document_to_collection(collection_id, document_id)
remove_document_from_collection(collection_id, document_id)
update_collection(collection_id, name=None, description=None)
delete_collection(collection_id)                 # removes only the container
promote_collection_to_lens(collection_id, parent_lens_id=None)
demote_lens(collection_id)
set_document_default_lens(document_id, lens_id=None)
```

**Read-only default.** Curation, enrichment, and reclassification require a separate
decision and review process. A prompt in this library proposes such actions; it does not
enact them.

## A.3 Resources (8) — `docray://`

| URI / template | Type | Serves |
|---|---|---|
| `docray://corpus/documents` | JSON | concrete — document index; start here |
| `docray://corpus/collections` | JSON | concrete — collection index |
| `docray://corpus/manifest` | JSON | concrete — shard map at the default width (8) |
| `docray://doc/{document_id}` | text | full text, one sentence per line; truncates explicitly at 200 000 chars |
| `docray://doc/{document_id}/sentences/{start}-{end}` | text | bounded, citable ordinal slice |
| `docray://doc/{document_id}/pages/{page}` | text | one source page |
| `docray://collection/{collection_id}` | JSON | one collection + members |
| `docray://corpus/shards/{shard_count}` | JSON | shard map for a map-reduce sweep |

Resources are addressing, not capability: same RLS scope, same payload cost as the
equivalent tool call.

## A.4 Prompts (4)

| Prompt | Arguments | Plan it returns |
|---|---|---|
| `summarize_document` | `document_id` | Eager: real title, sentence count, and the exact ordinal ranges to read, with outline provenance stated honestly |
| `ground_a_claim` | `claim`, `document_id?` | Search-then-verify, ending in "unsupported is reported, never asserted" |
| `survey_entities` | `document_id?` | Profiles over frequency; how to read `typeConsistency` and `resolved` |
| `sweep_the_corpus` | `shard_count=8` | Map-reduce plan plus the Σ-shards coverage proof |

---

# Appendix B — Cross-corpus composite protocol

This appendix records the rigorous method behind `P21`.

## B.1 Frame

1. Size the corpus (`corpus_stats`) and record each total's guarantee.
2. Enumerate the visible corpus and verify shard totals against the manifest.
3. Record collections and lenses, including the lens hierarchy (`parentLensId`).
4. Define "different domains" operationally — doc-ray exposes collections, tags, lenses, and a genre axis, but no domain field.
5. Record token scope and processing gaps.

## B.2 Select

### Node candidates

Prefer `entity_profiles` over `entity_frequency` when entity reconciliation matters, and
`lens_entities` when the work is scoped to a lens. For concepts not represented as
entities, use `term_frequency` to triage candidate terms and `lexeme_kwic` for contextual
inspection.

### Edge evidence

doc-ray exposes real edges within a lens, so co-occurrence no longer has to be
reconstructed by hand:

```text
direct_edge(A, B)      := entity_relations(lens, A) contains B      # NPMI-weighted
reachable(A, B, ≤k)    := related_entities(lens, A, hops=k) contains B   # k ≤ 3
cross_lens_link(A, B)  := allusions(A, lens_A) contains B in lens_B      # typed, non-transitive
```

Where no lens covers the candidates, fall back to the document-roster proxy:

```text
roster_edge(A, B)      := roster(A) ∩ roster(B) ≠ ∅
no_roster_edge(A, B)   := roster(A) ∩ roster(B) = ∅
no_ancestor(A, B)      := no shared collection and no shared lens
```

Both families detect **structural** association only. NPMI strength is not a claim of a
real relationship; a shared roster is co-occurrence, not conceptual relatedness; and an
allusion edge is a link, never an identity, and never chains.

### False-negative guards

Check:

- surface-form splitting (`entity_profiles.surfaceForms`);
- unresolved or contested entity typing (`typeConsistency`, `resolved: false`);
- lexical misses (substring semantics, folding, phrase boundaries);
- unembedded documents (semantic search returns a typed precondition failure, not `[]`);
- unannotated documents (invisible to sentence- and entity-level tools);
- access restrictions (RLS scope);
- stale Block F rollups (`refreshedAt`).

A candidate clears selection only after roster, variant, graph, and semantic probes all
fail to reveal an existing connection.

### Structural-similarity probe

Search for a subject-free description of the mechanism, failure mode, or dependency.
Example:

> A corrective signal that arrives only after the state it was meant to correct has already changed.

Retain candidates from unrelated documents and scopes.

## B.3 Ground

1. Inspect document outlines and note their `source`.
2. Read verbatim sentence ranges.
3. Use `document_evidence` for bounded per-document retrieval when appropriate.
4. Confirm the similarity remains present in the raw passages.
5. Discard candidates whose resemblance exists only in the model's paraphrase.

## B.4 Compose

```text
P_A  cited premise from source A
P_B  cited premise from source B
∴ C  new composite claim
```

The composite is load-bearing only when deleting either premise breaks the inference.

## B.5 Falsify

1. State the strongest defeater.
2. Search for it semantically and lexically.
3. Report queries and hit counts.
4. Treat no hit as silence **only** when P33 is satisfied; otherwise report `NOT_SEARCHED`.
5. Preserve contradictions.

## B.6 Locate

Explain what the composite predicts, forbids, or permits, then provide a test that could
validate it as the corpus or external evidence grows.

## B.7 Run ledger

Record:

- shard count and total visible documents, with the guarantee behind each total;
- documents touched and coverage fraction (denominator: the exact `documents` count);
- candidates generated;
- discards by stage;
- survivors;
- degraded modes;
- failed calls and their typed kinds.

## B.8 Limitations

- A graph reconstructed from co-occurrence is an association structure, not a conceptual map — this remains true of `entity_relations` and `related_entities`, which are NPMI rollups.
- Collections and lenses are curation structures; a lens is a resolution scope, not a taxonomy.
- Corpus visibility is token-scoped.
- Semantic retrieval may be unavailable (no vectors, model cutover, capability absent).
- An absent edge is defeasible evidence, not proof of no relationship — especially when the rollup's `refreshedAt` is stale.

---

# Appendix C — Knowledge matrix

## C.1 Operational knowledge: FAZER / NÃO FAZER

| Dimension | FAZER | NÃO FAZER |
|---|---|---|
| **O QUE** | Actions that constitute the practice | Similar-looking actions that are not the practice |
| **POR QUÊ** | Justification and mechanism | Costs, trade-offs, and invalid rationales |
| **QUANDO** | Enabling conditions | Disqualifying conditions |
| **COMO** | Correct execution | Errors and anti-patterns |
| **CONSEQUÊNCIAS** | What follows from doing it | What follows from not doing it |

Recommended order:

`O QUE → POR QUÊ → QUANDO → COMO → CONSEQUÊNCIAS`

The negative column is load-bearing because boundaries distinguish operational knowledge
from familiarity.

## C.2 Propositional knowledge: É / NÃO É

When the subject is a fact, theorem, historical event, object, or classification rather
than a practice, add a separate polarity:

| Dimension | É | NÃO É |
|---|---|---|
| Identity | What the thing is | Confusable things it is not |
| Conditions | Conditions under which the proposition holds | Conditions that invalidate it |
| Evidence | Supporting observations or proofs | Evidence that would defeat it |
| Consequences | What follows if true | What follows if false |

## C.3 Transmission test

A learner or agent should be able to teach:

- the affirmative structure;
- the negative boundaries;
- failure conditions;
- trade-offs;
- correction procedure;
- a novel transfer case.

Fluent explanation alone is not sufficient when the system has no independent
observation, correction, or persistence channel.

## C.4 AI-specific cautions

- Generated self-reports are not privileged introspection.
- Reported consequences are not the same as predicted-and-observed consequences.
- Correction should be tested across a persistence boundary.
- Transfer is difficult to establish when the training corpus is unknown.
- Use a profile vector rather than one monotonic proficiency level.
- Test adversarial, out-of-corpus confusables where possible.

---

# Appendix D — Corpus inventory snapshot

> A snapshot of the corpus visible to one token on **2026-07-31**, not a live inventory
> and not the corpus any other token sees. Counts change continuously. Refresh with the
> recipe in D.5 before relying on any number here.

## D.1 Corpus totals

| Figure | Value | Guarantee |
|---|---:|---|
| Documents | 783 | `exact` (counted live) |
| Sentences | 3,311,888 | `rollup` (exact once each document's pipeline job has committed) |
| Lexemes | 453,662 | `debounced` (600 s window; reported `stale` at read time) |
| Lexeme occurrences | 72,616,830 | `rollup` |

Use `documents` as the denominator for coverage fractions; it is the only exact figure.

## D.2 Lenses

| Lens | Documents | Parent | Kind |
|---|---:|---|---|
| `global` | 0 | — | manual (reserved: real-world identity) |
| Dune Series | 36 | — | smart (`tags: Dune Series`) |
| Dune: Expanded Universe | 21 | Dune Series | smart (`tags: Expanded Universe`) |

The `global` lens holds shared real-world identities and reports zero *member* documents
by design; that is not a defect, but it does mean lens membership alone tells you nothing
about how many documents project globally.

## D.3 Subject collections (49 visible collections total)

| Collection | Documents | Rule tag |
|---|---:|---|
| Research Papers | 84 | Research Paper (cross-cutting format) |
| Mathematics | 66 | Mathematics |
| Software & Systems Engineering | 63 | Systems Engineering |
| Technical & Programming | 56 | Programming |
| AI & ML Research | 51 | AI Research |
| Marketing & Branding | 48 | Marketing |
| Design & UX | 42 | UX Design |
| Graphic Design & Typography | 40 | Graphic Design |
| Agentic AI | 38 | Agentic AI |
| Dune Series | 36 | Dune Series |
| Applied AI & Machine Learning | 27 | Applied AI |
| Automation & DevOps | 26 | Automation |
| Computer Graphics & Game Development | 25 | Computer Graphics |
| Dune: Expanded Universe | 21 | Expanded Universe |
| Organizational Strategy & Change | 17 | Organizational Strategy |
| Startups & Entrepreneurship | 17 | Entrepreneurship |
| Linguistics & Semiotics | 15 | Linguistics |
| AI Security & Governance | 13 | AI Security |
| Domain-Driven Design | 13 | Domain-Driven Design |
| Natural Language Processing | 13 | Natural Language Processing |
| AI & Business | 12 | Artificial Intelligence |
| Presentation & Communication | 12 | Communication |
| Databases & Data Engineering | 11 | Databases |
| Security & Cybersecurity | 10 | Cybersecurity |
| Visual Arts & Illustration | 10 | Visual Arts |
| Writing Craft | 10 | Writing Craft |
| Audio & Signal Processing | 9 | Audio Processing |
| Finance & Valuation | 9 | Finance |
| Human Resources & People Management | 9 | Human Resources |
| Cognitive Science | 8 | Cognitive Science |
| Psychology | 8 | Psychology |
| Business Analysis & Enterprise Architecture | 7 | Business Analysis |
| Formal Methods & CS Theory | 7 | CS Theory |
| Dune Novels | 6 | Dune Novel |
| Project & Programme Management | 6 | Project Management |
| Philosophy of Language & Mind | 5 | Philosophy |
| Careers & Workplace | 4 | Workplace |
| Compilers & Toolchains | 4 | Compilers |
| Gamification & Engagement Design | 4 | Gamification |
| Product Management | 3 | Product Management |
| Voice & Singing | 2 | Voice & Singing |
| Scripture & Sacred Texts | 1 | Scripture |

Collections overlap deliberately (a title may carry several subject tags, and Research
Papers is a format collection spanning fields). **Counts must not be summed.**

## D.4 Processing and review queues

| Queue | Documents | Retrieval effect |
|---|---:|---|
| Annotation Backlog | 31 | Extracted but not annotated: invisible to sentence- and entity-level tools. |
| Archive — Unreadable | 22 | Extraction yielded nothing usable; retired from the active corpus. |
| Extraction Failures — Repair Queue | 22 | No searchable content at all. |
| Duplicates — Quarantine | 11 | Confirmed duplicates, held for provenance. |
| Needs Identification — Triage Queue | 3 | Title is a filename artifact; the work is unidentified. |
| Working Notes — Unreviewed Synthesis | 0 | Unreviewed synthesis; retrievable but carries no independent authority. |

Roughly 75 of 783 documents (~10%) sit in a queue that makes them wholly or partly
invisible to sentence- and entity-level retrieval. Queue membership may overlap, so treat
that figure as an order of magnitude, not a sum.

## D.5 Operational implications

- The annotation backlog makes sentence- and entity-level retrieval silently partial: a document can be listed, searched by full text, and still contribute no sentences or entities.
- Extraction failures, archived-unreadable documents, and unidentified titles all weaken absence claims. P33 requires accounting for them explicitly.
- Collection overlaps cause double counting; never sum the tables above.
- A lens's `documentCount` is scoped to the token's visibility, and the `global` lens's zero is structural.
- Refresh the inventory and the tool contract before every consequential run:

```text
corpus_stats()                    # totals + guarantees; read `stale`
list_collections(limit=200)       # page with the last row's cursor
list_lenses(limit=200)
corpus_manifest(shard_count=8)    # per-shard counts that must sum to totalDocuments
```

---

# Appendix E — Prompt index

| ID | Name | Section |
|---|---|---|
| P01 | Corpus capability audit | 5 |
| P02 | Prompt-engineering corpus audit | 5 |
| P03 | Coverage and gap audit | 5 |
| P04 | Grounded question | 6 |
| P05 | Exact wording extractor | 6 |
| P06 | Evidence map | 6 |
| P07 | Source-bounded answer with negative findings | 6 |
| P08 | Steelman from source | 7 |
| P09 | Contradiction finder | 7 |
| P10 | Briefing pack to executive one-pager | 7 |
| P11 | Concept genealogy | 7 |
| P12 | Synthesis matrix | 7 |
| P13 | Entity dossier | 8 |
| P14 | Lens-scoped relationship map | 8 |
| P15 | Reading-path generator | 8 |
| P16 | Teach from corpus | 9 |
| P17 | Ten-cell knowledge cartridge | 9 |
| P18 | Teach-back validation | 9 |
| P19 | Corpus-grounded ideation | 10 |
| P20 | Cross-modal concept board | 10 |
| P21 | Cross-corpus composite generator | 10 |
| P22 | Duplicate and identity-drift check | 11 |
| P23 | Corpus drift monitor specification | 11 |
| P24 | Decision provenance and human-awareness system | 11 |
| P25 | Corpus-to-Agentic-AI feasibility study | 12 |
| P26 | Agentic library and self-defined A2A pipelines | 12 |
| P27 | Corpus-grounded architecture oracle | 12 |
| P28 | Corpus-grounded monograph | 13 |
| P29 | Knowledge cartridge document | 13 |
| P30 | Corpus-grounded three-chapter Dune fan-fiction novel | 14 |
| P31 | Generate a reusable prompt set from the corpus | 15 |
| P32 | Corpus sweep with coverage proof | 5 |
| P33 | Absence claim protocol | 6 |
| P34 | Lens identity audit | 8 |
| P35 | Cross-lens allusion review | 8 |
| P36 | Visual caption trust review | 11 |
| P37 | Structural evidence extraction | 8 |

---

# End of library

When extending this file: add new prompts to the relevant functional section, assign the
next prompt ID, register it in Appendix E, state required evidence and failure modes, and
update the composition workflows when the new prompt participates in a repeatable chain.

When the MCP surface changes, §3, §4, and Appendix A are the parts that go stale.
Re-derive them from the tool registries and [docs/mcp-service.md](mcp-service.md), and
bump the `verified_against` block in the frontmatter.
