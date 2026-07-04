---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-04"
---

# Canonicalization Pipeline
### Frontier model as resolver, and the remissive index that maps every form to its canonical

The organizing principle:

> The frontier model **resolves and enriches — it never freely invents identity.**
> It picks from a constrained candidate set or abstains, and everything it emits is
> written back. The expensive, non-deterministic call happens **once per novel form**;
> after that, resolution is a cheap index lookup.

That turns the model's non-determinism from a liability into a one-time cost. You are not
running a frontier model over ten thousand documents forever — you are using it to **build
and grow the canonical layer**, and the remissive index it fills serves every future
lookup without it. The corpus converges as the index accumulates.

---

## The flow

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ STAGE 0 · SEED THE CANONICAL REGISTRY   (Wikidata + Wikipedia, domain-scoped)│
 │   Enumerate the war's entity classes (people, units, weapons, places,       │
 │   orgs, events) → pull Wikidata Q-ids + multilingual labels/aliases.        │
 │   The registry has a canonical spine BEFORE any messy source is touched.    │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ STAGE 1 · CHEAP MENTION EXTRACTION   (fast NER/POS/lemma, per doc, at scale) │
 │   NOT the frontier model. spaCy-class models, per language, version pinned. │
 │   Emits Tier-1 mentions = divergent observations. Divergence is fine here.  │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ STAGE 2 · CANDIDATE GENERATION / BLOCKING   (deterministic, no LLM)         │
 │   For each mention, shortlist canonical candidates by:                       │
 │     1. remissive-index lookup on the normalized form   ← the flywheel entry │
 │     2. embedding similarity (mention-in-context ↔ canonical)                │
 │     3. Wikidata search for anything still unmatched                          │
 │   Thousands of entities → a handful of candidates per mention.              │
 └───────────────────────────────┬──────────────────────────────────────────┘
                     ┌───────────┴────────────┐
             exact hit, high conf        ambiguous / novel / conflicting
                     │                        │
                     ▼                        ▼
             auto-link (skip LLM)   ┌──────────────────────────────────────────┐
                     │             │ STAGE 3 · FRONTIER RESOLUTION              │
                     │             │   Input: mention IN ITS SENTENCE + the     │
                     │             │   candidate canonicals (with descriptions).│
                     │             │   Output: pick one · "new entity" · "abstain".│
                     │             │   Constrained choice, never a freehand id. │
                     │             └───────────────┬──────────────────────────┘
                     │                             ▼
                     │             ┌──────────────────────────────────────────┐
                     │             │ STAGE 4 · FRONTIER ENRICHMENT             │
                     │             │   • harvest new aliases/epithets it saw    │
                     │             │   • propose new canonical entries          │
                     │             │   • cross-lingual link (uk/ru/en → 1 id)   │
                     │             │   • coref: "the president"/"he" → entity   │
                     │             │   • (optional) relations → knowledge graph │
                     │             └───────────────┬──────────────────────────┘
                     └──────────────┬──────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ STAGE 5 · ADJUDICATE & MERGE   (the decision layer, deterministic + auditable)│
 │   confidence ≥ θ  → auto-commit                                              │
 │   confidence < θ / conflicting candidates → HUMAN REVIEW QUEUE              │
 │   duplicate canonicals → merge op, repoint edges, logged in provenance      │
 └───────────────────────────────┬──────────────────────────────────────────┘
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ STAGE 6 · MATERIALIZE THE REMISSIVE INDEX   (forms → canonical)             │
 │   Every resolved/harvested form is written to form_index.                   │
 │   ── which is exactly what STAGE 2 reads first next batch ──► the flywheel  │
 └──────────────────────────────────────────────────────────────────────────┘
```

**Where the frontier model sits, and why.** Extraction and blocking are cheap, parallel,
and deterministic — keep them off the LLM and run them over the whole corpus. The frontier
model runs **only on the residue**: the mentions Stage 2 couldn't confidently resolve, plus
the enrichment pass. That is where its judgment is irreplaceable — reading "the president"
in *this* sentence, knowing "Kyiv" here means the government not the city, collapsing
"Артёмовск" and "Bakhmut" and "Бахмут" onto one entity. One frontier model handles all
languages natively, so the cross-lingual layer is a strength, not a separate subsystem.

**The constrained-choice rule is what keeps it honest.** The model never emits a raw id
from its own memory. It selects from the Stage-2 candidate set, says "new entity," or says
"abstain." A selection is verifiable; a hallucinated Q-id is not. Abstentions go to review
rather than becoming a confident wrong link that permanently pollutes the canonical layer.

This maps straight onto doc-ray: Stage 1 mentions are `entity_mention`; the resolution edge
is `entity_link` pointing at a canonical id; the two new pieces are the **canonical
registry** and the **remissive index** below.

---

## The remissive index

Two tables. The **registry** is the set of real things (one row each). The **remissive
index** is the reverse lookup: every surface form → the canonical entry it means.

```sql
-- Tier 2 · the canonical registry: one row per real thing
CREATE TABLE canonical (
    id           bigserial PRIMARY KEY,
    wikidata_id  text UNIQUE,          -- 'Q188817'  (NULL for local-only entities)
    preferred    text NOT NULL,        -- 'Volodymyr Zelensky'
    entity_type  text NOT NULL,        -- PERSON|PLACE|WEAPON|UNIT|ORG|EVENT|CONCEPT
    description  text,
    lang_primary text
);

-- THE REMISSIVE INDEX · every form that maps to a canonical
CREATE TABLE form_index (
    form_norm    text   NOT NULL,      -- normalized lookup key: NFC + casefold + translit
    form_surface text   NOT NULL,      -- as seen: 'Zelenskyy' · 'the president' · 'Артёмовск'
    canonical_id bigint NOT NULL REFERENCES canonical(id) ON DELETE CASCADE,
    lang         text,
    form_type    text   NOT NULL,      -- name|alias|acronym|epithet|translit|descriptor|misspelling
    confidence   real   NOT NULL DEFAULT 1.0,
    occurrences  int    NOT NULL DEFAULT 0,      -- corpus frequency of this exact form
    source_types text[] NOT NULL DEFAULT '{}',   -- {wikipedia,news,tweet,book}
    first_seen   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (form_norm, canonical_id, lang)
);
CREATE INDEX idx_form_lookup       ON form_index (form_norm);      -- form → canonical (resolve)
CREATE INDEX idx_form_by_canonical ON form_index (canonical_id);   -- canonical → all its forms
```

It works in **both directions**, which is the point of a remissive index:

- **form → canonical** (the resolver, and the Stage-2 first pass): normalize the incoming
  surface string, look it up, get the canonical id. This is what lets most mentions skip the
  frontier model entirely.
- **canonical → forms** (the cross-reference view): given an entity, list every way the
  corpus refers to it. This is the classic *índice remissivo* entry — a headword and its
  variants — rendered from `idx_form_by_canonical`:

```
Zelensky, Volodymyr                      [PERSON · Q188817]
   ├─ names ....... Zelenskyy · Zelenskiy · Zelensky
   ├─ descriptors . President Zelensky · the Ukrainian president · Ukraine's leader
   ├─ uk .......... Володимир Зеленський
   ├─ ru .......... Владимир Зеленский
   ├─ epithets .... Ze · Zelya                         (source: tweets)
   └─ 4,182 occurrences · 1,190 documents · sources {wikipedia, news, tweet, book}
```

Every line in that entry is a `form_index` row. When the frontier model resolves a new
epithet from a tweet, it adds a row; the next time that epithet appears anywhere in the
corpus, Stage 2 resolves it by lookup and the model never sees it again.

`form_type` is what keeps the index from flattening genuinely different kinds of reference
into one bucket — a misspelling, a Russian exonym, and an informal epithet all resolve to
the same entity but are *tagged* by how they refer, so you can query "all Ukrainian-language
forms of X" or "descriptor phrases, not proper names" or filter out low-confidence
misspellings.

---

## Build this first

Seed `canonical` from the Wikipedia + Wikidata subset you already have — that is your
cleanest, most entity-dense source, and it hands you multilingual aliases for free. Wire the
**remissive-index-first** candidate step so resolution is a lookup by default. Then point the
frontier model at only what the lookup misses, running it in constrained-choice mode
(select / new / abstain) and writing every resolution and harvested alias straight back into
`form_index`. Start with people and places (highest payoff, cleanest Wikidata coverage),
then units and weapons, then concepts.

The index fills fastest on the entities that appear most, so the frontier model's call
volume drops sharply after the first pass — you pay for judgment once and collect canonical
lookups forever. That is the corpus draining from a swamp of divergent mentions into a single
stable spine, and the remissive index is both the map of it and the engine that builds it.

---

## What actually shipped, and where it diverged

The build order above (seed → index-first lookup → constrained frontier resolver
→ write-back) landed as designed (`canonical_seed`, `candidate_generation`,
`resolver_claude` + `resolution`). Two things emerged during live-fire use that
the original design didn't anticipate, and both are now part of the pipeline:

**Stage 1.5 — registry-driven term scan.** The corpus isn't only character-
driven fiction; a technical document's real vocabulary is lowercase common
nouns ("uncial", "parchment", "versal"), and NER (Stage 1) only tags
proper-noun-shaped spans. No amount of seeding `canonical` helped, because
those terms never became `entity_mention` rows to resolve in the first place.
`application/term_scan.py` closes the gap deterministically: it scans a
document's text for surfaces `form_index` already knows and emits mentions
under a dedicated `layer='term'` annotation, which Stage 2 then links through
the ordinary pathway — it only *emits*, never links directly, so ambiguous
registry hits still become residue for the frontier/review rather than being
force-linked. Guards (word boundaries, longest-match-wins, a length/stopword
floor for single-token forms, case-exact acronyms, and a nominal-POS filter
using the document's own token layer) keep polysemous KB aliases from
matching verb usage — the live catcher was "a loose end hanging from…"
false-linking to *tapestry* before the POS filter was added. Runs as part of
the `canonicalize` worker job, immediately before Stage-2 auto-link.

**Seeding needed a human gate the design didn't call out explicitly.**
Automating Stage 0 end-to-end turned out to be actively unsafe: fuzzy
Wikidata search repeatedly drifted to the wrong entity from live-fire seed
runs — a lettering term "versal" resolved to the Wikidata item for
*Versailles*; a bare surname "Albertus" resolved to *Albertus Magnus*, the
13th-century friar, instead of the typeface. Both are exactly the kind of
plausible-but-wrong identity PALS's Law (`CLAUDE.md`) warns about, except the
untrusted output here is a *search index's* ranking, not an LLM's — the same
discipline applies. The fix is a staging table, not a smarter search: a
`seed_suggest` worker job mines candidates and resolves each one through its
**Wikipedia article title** via `adapters/wikipedia.py` (pageprops; exact
identity, and it refuses disambiguation pages outright — the mechanism that
would have caught the "Albertus" case) rather than the fuzzy `wbsearchentities`
call `canonical_seed` uses for direct QID/term seeding. Every candidate lands
in `seed_suggestion` (migration 025) as `pending`, with the resolved
description shown as the audit surface, so a human approves or rejects before
anything reaches `canonical` — `seed_approved` then seeds only what was
approved through the same `canonical_seed` path this design specified. The
webapp's `/doc/<id>/seeding` screen is this workflow's interface; see
[`webapp_user_manual.md`](webapp_user_manual.md) §3 for the curator's-eye view
and worked examples of what the audit step actually catches.
