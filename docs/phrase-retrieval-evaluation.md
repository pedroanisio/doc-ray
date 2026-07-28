---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 (1M context) via Claude Code"
  date: "2026-07-28"
---

# Phrase-retrieval evaluation harness

[← back to README](../README.md) · [MCP evaluation](mcp-evaluation.md) · [NLP architecture](nlp_architecture.md)

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Why this exists

The phrase-retrieval programme ships in two stages. Stage 1 — verified phrase
matching over `sentences.text` — is released: `search_sentences(match="phrase")`
and `term_frequency`'s occurrence counts. Stage 2 is a positional token engine:
a surface-term registry, a backfill over the token fact table, and a large new
composite index.

Stage 2 is expensive, and the plan that proposed it recorded only *medium*
confidence in its index and schema shape "until real query plans are measured".
This harness produces those measurements so the decision is made against numbers
rather than against the assumption the plan started from.

It ships **no feature code**. Nothing in the GraphQL, MCP, webapp or SDK surface
changes because of it; the deliverable is a measurement, a dated report, and a
recorded decision.

## What it measures

Seven fixed query classes, chosen so that each isolates a different way phrase
retrieval can be expensive:

| Class | Phrase | What it isolates |
|---|---|---|
| `rare_phrase` | `Model Context Protocol` | every word uncommon — the candidate clause should be highly selective |
| `mid_phrase` | `algorithm design` | an ordinary technical bigram, the common research query |
| `common_words` | `not happy` | candidates greatly outnumber real occurrences |
| `all_short_words` | `of it` | nothing is selective; the cap, not the index, bounds it |
| `joiner` | `COVID-19` | the profile splits on the hyphen, so one term becomes an adjacency test |
| `confusable` | `Hick's law` | ASCII and typographic apostrophes must be one phrase |
| `single_rare_control` | `ngram` | single-word control, isolating phrase cost from lookup cost |

For each it records p50/p95 over warm runs, how many candidates the scan
returned, whether the candidate cap truncated it, word-boundary false positives,
and — the part that explains the rest — **which access path the planner chose**.

A latency on its own cannot tell an operator what to fix. `54 ms` and `> 5000 ms`
against the same code mean very different things once you know both were
sequential scans of the same relation.

## The safety contract

The production leg reads a live corpus, so its guarantees are enforced rather
than promised:

- The session is set `READ ONLY`, and the harness **reads the setting back**
  before measuring anything. A session that reports otherwise aborts the run.
- A defensive `statement_timeout` (5 s) bounds every statement.
- Every statement is screened by `guard_sql` before execution. Anything that is
  not a pure read — including `EXPLAIN ANALYZE` of a write, and multi-statement
  strings — raises rather than reaching the server.
- The connection string comes from the environment and is never logged; the
  banner prints host and database only.
- The harness refuses to benchmark rows it cannot see. RLS is FORCEd on
  `sentences`, and a role with no readable documents would otherwise measure an
  empty result set in microseconds and publish it as a result.
- If no class matches any candidate, the verdict is `OPERATOR`, never `NO_GO`:
  fast-because-there-was-nothing-to-find is not fast.

`SET ROLE` and `set_config` are transactional in PostgreSQL, so a statement
timeout that aborts the transaction also discards the measured role and RLS
principal. The harness re-establishes both after any cancel — without that,
every class after the first timeout runs unscoped and silently measures nothing.

## Running it

```bash
# Isolated container leg — no credentials, no production contact.
python scripts/eval_phrase_retrieval.py

# Production leg, read-only. The role matters: `app.principal_id` is only
# honoured for `docray_api`, so measuring as any other role ignores the
# principal and RLS hides the corpus.
python scripts/eval_phrase_retrieval.py --prod --explain \
    --role docray_api --principal <admin-uuid> \
    --report docs/eval/phrase-retrieval-$(date -u +%F).md
```

| Flag | Meaning |
|---|---|
| `--prod` | measure the live corpus; prints the rules-of-engagement banner |
| `--runs N` | warm runs per class (default 20, per OD-2) |
| `--role NAME` | database role to assume; `docray_api` to measure the API's own read path |
| `--principal UUID` | RLS principal to measure as |
| `--explain` | capture the chosen plan and an `EXPLAIN (ANALYZE, BUFFERS)` per class |
| `--report PATH` | write the dated Markdown report |
| `--lemma-requirement` | record that the operator has stated a lemma-phrase requirement (FR-5) |
| `--date` | pin the report date instead of using today (determinism) |

## Reading the report

Every number carries provenance — a table whose header names a Source column, or
an inline `(source: …)` citation. This is enforced, not conventional: the
generator runs `find_unprovenanced_numbers` over its own output and **refuses to
write a report** that states a bare number. The suite plants one to prove the
check can fail.

Sections that could not be measured say so. An unanalyzed relation reports
`reltuples = -1`, meaning *unknown*, not *empty*; rather than pricing the stage-2
index at zero bytes and presenting that as a measurement, the pricing section
renders as not measured.

## The go/no-go criteria

`scripts/_phrase_eval_criteria.decide` is a pure function over the measured
matrix, so the verdict is reproducible and reviewable:

- **NO_GO** — stay on stage 1: p95 within budget for every class except at most
  the tolerated non-selective one, and the correctness suite clean.
- **GO** — a structural budget failure outside the tolerated class (over budget
  while already capped, or unable to finish inside the timeout), or an
  operator-stated lemma-phrase requirement stage 1 cannot serve.
- **OPERATOR** — anything else, including over-budget-but-not-truncated,
  boundary false positives, and a P6/P7 disagreement.

Three points the specification left ambiguous, resolved and recorded:

- **A timeout is a structural failure.** The candidate cap is a `LIMIT` on rows
  *returned*; it cannot shorten a scan that must read the whole relation to find
  them.
- **The tolerated class keeps its exemption even when it saturates the cap.**
  Reading "any structural failure ⇒ GO" literally would make the NO_GO clause's
  explicit exemption unreachable, so the specific rule wins over the general one.
- **`agreement=None` is "not comparable", not "disagreed".** Above the shared
  2,001 candidate cap the two surfaces order candidates differently and sample
  different subsets, so equality is not expected and must not score as a
  correctness failure.

## What the first run found

The dated report is [docs/eval/phrase-retrieval-2026-07-28.md](eval/phrase-retrieval-2026-07-28.md);
the decision is **D-035** in `engineering_decisions.yml`, status `proposed`.

Summarised: six of seven classes exceed a 5 s timeout against a 300–400 ms
budget, and all seven plan a `Seq Scan` over `sentences`. The one class that
meets the budget is the one the specification expected to be *worst* — `of it`
fills the candidate cap immediately, so the `LIMIT` stops the scan early. A rare
phrase is the expensive case, because the scan must read everything to establish
that little matches.

The bottleneck is candidate *generation*, not verification: verification is pure
matching over at most 2,001 texts, bounded by construction. That points at the
leading-wildcard `ILIKE` superset — the offender D-025 already recorded as
needing a trigram or FTS index — rather than at the substrate stage 2 would
replace.

## Related

- [MCP evaluation harness](mcp-evaluation.md) — the same measurement-first
  pattern applied to the agent-facing tool surface.
- [NLP architecture](nlp_architecture.md) — the annotation layers stage 2 would
  build on.
- `engineering_decisions.yml` — D-025 (read-path budget and the rollup doctrine),
  D-035 (this evaluation).
