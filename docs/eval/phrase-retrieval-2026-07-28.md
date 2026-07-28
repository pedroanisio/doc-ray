---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 5 via Claude Code"
  date: "2026-07-28"
---

# Phrase-retrieval evaluation — stage 1 measured, stage 2 priced

Issue #47 (Retrieval · P8). This report is the gate record for #48: it measures
the shipped verified-phrase path, prices the positional token engine, and states
a go/no-go against fixed criteria rather than judgement.

**Leg:** production (read-only) · **DB:** db-pgsql-tor1-55041-do-user-14287144-0.h.db.ondigitalocean.com/docray (source: `DATABASE_URL`)

Every figure below is produced by `scripts/eval_phrase_retrieval.py`; re-running
that command regenerates this report. Bare numbers are a defect the generator
itself rejects (`_phrase_eval_report.find_unprovenanced_numbers`).

## Verdict — GO

> Structural budget failure in common_words, confusable, joiner, mid_phrase, rare_phrase, single_rare_control: the candidate scan timed out before completing against a 400 ms budget, and the cap bounds rows returned rather than rows scanned, so no cap tuning can hide it. (source: `_phrase_eval_criteria.decide`, FR-5)

Recorded as claim **D-035** in `engineering_decisions.yml`,
status `proposed` until the operator responds on #48.

## Latency — server-side (GraphQL resolver path)

The budget line is the read-surface target from D-025's rationale
(source: `engineering_decisions.yml`, D-025).

| Class | Phrase | Candidates | p50 (ms) | p95 (ms) | Runs | Outcome | Boundary FPs | Source |
|---|---|---|---|---|---|---|---|---|
| rare_phrase | `Model Context Protocol` | 358 | ≥ 5000.0 | ≥ 5000.0 | 20 | **timed out** | 0 | server |
| mid_phrase | `algorithm design` | 0 | ≥ 5000.0 | ≥ 5000.0 | 20 | **timed out** | 0 | server |
| common_words | `not happy` | 0 | ≥ 5000.0 | ≥ 5000.0 | 20 | **timed out** | 0 | server |
| all_short_words | `of it` | 2001 | 50.9 | 60.4 | 20 | capped | 0 | server |
| joiner | `COVID-19` | 0 | ≥ 5000.0 | ≥ 5000.0 | 20 | **timed out** | 0 | server |
| confusable | `Hick's law` | 0 | ≥ 5000.0 | ≥ 5000.0 | 20 | **timed out** | 0 | server |
| single_rare_control | `ngram` | 247 | ≥ 5000.0 | ≥ 5000.0 | 20 | **timed out** | 0 | server |

A **timed out** row states a *floor*, not a duration: the scan was cancelled at the statement timeout, so its true cost is unknown and at least the value shown. Its candidate count comes from the warm-up run, which is the only attempt that completed, so it is also a floor.

No class sits close enough to the budget line for a larger sample to change the verdict, so OD-2's escalation was not triggered (source: `_phrase_eval_criteria.needs_escalation`).

## Latency — end to end (MCP over loopback)

OD-1 was resolved as *both*: the budget is user-facing, the plans are
server-side, so the two are reported as separate columns rather than merged.

_The mcp leg was not measured in this run._

## Correctness

Zero word-boundary false positives is FR-1's precondition; the `Boundary FPs`
column above carries the measurement per class. Agreement between the P6
occurrence list and the P7 counts is only asserted where neither side truncated
— above the shared candidate cap the two order their candidates differently and
sample different subsets, so equality is not expected there
(source: `eval_phrase_retrieval.py`, correctness leg).

## Access paths

| Class | Access path | Uses an index? | Source |
|---|---|---|---|
| rare_phrase | Seq Scan | **no index** | `EXPLAIN`, this harness |
| mid_phrase | Seq Scan | **no index** | `EXPLAIN`, this harness |
| common_words | Seq Scan | **no index** | `EXPLAIN`, this harness |
| all_short_words | Seq Scan | **no index** | `EXPLAIN`, this harness |
| joiner | Seq Scan | **no index** | `EXPLAIN`, this harness |
| confusable | Seq Scan | **no index** | `EXPLAIN`, this harness |
| single_rare_control | Seq Scan | **no index** | `EXPLAIN`, this harness |

Every class marked **no index** reads the `sentences` fact table end to end: rare_phrase, mid_phrase, common_words, all_short_words, joiner, confusable, single_rare_control. That is the cost being measured above, and it is a property of how candidates are generated — not of the verification step, which is pure and bounded by the candidate cap.

## Query plans

### rare_phrase

```
Limit  (cost=12.75..97584.03 rows=1 width=162)
  ->  Seq Scan on sentences s  (cost=12.75..97584.03 rows=1 width=162)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%protocol%'::text) AND (text ~~* '%context%'::text) AND (text ~~* '%model%'::text))
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16)

-- with ANALYZE --
Limit  (cost=12.75..97584.03 rows=1 width=162) (actual time=190.914..4724.196 rows=358.00 loops=1)
  Buffers: shared hit=61805
  ->  Seq Scan on sentences s  (cost=12.75..97584.03 rows=1 width=162) (actual time=190.912..4724.027 rows=358.00 loops=1)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%protocol%'::text) AND (text ~~* '%context%'::text) AND (text ~~* '%model%'::text))
        Rows Removed by Filter: 1974077
        Buffers: shared hit=61805
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16) (actual time=1.677..1.723 rows=498.00 loops=1)
                Buffers: shared hit=144
Planning Time: 0.142 ms
Execution Time: 4724.337 ms
```

### mid_phrase

```
Limit  (cost=12.75..92650.74 rows=1 width=162)
  ->  Seq Scan on sentences s  (cost=12.75..92650.74 rows=1 width=162)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%algorithm%'::text) AND (text ~~* '%design%'::text))
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16)

-- with ANALYZE --
EXPLAIN (ANALYZE) did not complete within 5000 ms.
```

### common_words

```
Limit  (cost=12.75..92650.74 rows=63 width=162)
  ->  Seq Scan on sentences s  (cost=12.75..92650.74 rows=63 width=162)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%happy%'::text) AND (text ~~* '%not%'::text))
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16)

-- with ANALYZE --
EXPLAIN (ANALYZE) did not complete within 5000 ms.
```

### all_short_words

```
Limit  (cost=12.75..4709.67 rows=2001 width=162)
  ->  Seq Scan on sentences s  (cost=12.75..92650.74 rows=39466 width=162)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%of%'::text) AND (text ~~* '%it%'::text))
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16)

-- with ANALYZE --
Limit  (cost=12.75..4709.67 rows=2001 width=162) (actual time=2.053..38.722 rows=2001.00 loops=1)
  Buffers: shared hit=486
  ->  Seq Scan on sentences s  (cost=12.75..92650.74 rows=39466 width=162) (actual time=2.051..38.516 rows=2001.00 loops=1)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%of%'::text) AND (text ~~* '%it%'::text))
        Rows Removed by Filter: 13043
        Buffers: shared hit=486
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16) (actual time=1.709..1.756 rows=498.00 loops=1)
                Buffers: shared hit=144
Planning Time: 0.142 ms
Execution Time: 38.897 ms
```

### joiner

```
Limit  (cost=12.75..92650.74 rows=316 width=162)
  ->  Seq Scan on sentences s  (cost=12.75..92650.74 rows=316 width=162)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%covid%'::text) AND (text ~~* '%19%'::text))
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16)

-- with ANALYZE --
EXPLAIN (ANALYZE) did not complete within 5000 ms.
```

### confusable

```
Limit  (cost=12.75..97584.03 rows=316 width=162)
  ->  Seq Scan on sentences s  (cost=12.75..97584.03 rows=316 width=162)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%hick%'::text) AND (text ~~* '%law%'::text) AND (text ~~* '%s%'::text))
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16)

-- with ANALYZE --
EXPLAIN (ANALYZE) did not complete within 5000 ms.
```

### single_rare_control

```
Limit  (cost=12.75..87717.46 rows=1579 width=162)
  ->  Seq Scan on sentences s  (cost=12.75..87717.46 rows=1579 width=162)
        Filter: ((ANY (document_id = (hashed SubPlan 1).col1)) AND (text ~~* '%ngram%'::text))
        SubPlan 1
          ->  Function Scan on docray_readable_documents  (cost=0.25..10.25 rows=1000 width=16)

-- with ANALYZE --
EXPLAIN (ANALYZE) did not complete within 5000 ms.
```

## Stage-2 pricing (FR-4)

All stage-2 figures are **estimates**: row widths are nominal and `reltuples`
is itself an estimate, not a count.

| Quantity | Value | Source |
|---|---|---|
| Positional index rows | 54442884 | pg_class.reltuples + TABLESAMPLE SYSTEM (1), this harness |
| Positional index bytes | 4137659184 | derived, estimate |
| Surface registry rows | 1922900 | pg_class.reltuples + TABLESAMPLE SYSTEM (1), this harness |
| Surface registry bytes | 123065600 | derived, estimate |

Arithmetic, shown in full so the estimate can be checked rather than trusted:

```
index rows = token rows = 54442884 (pg_class.reltuples); index bytes = 54442884 x 76 B/row = 4137659184 B; registry rows = distinct surfaces = 1922900 (bounded TABLESAMPLE extrapolation); registry bytes = 1922900 x 64 B/row = 123065600 B; corpus shape = 1966114 sentences at 134.3 average chars.
```

## Read-only evidence (NFR-3)

The production leg runs inside a read-only transaction with a defensive
statement timeout, and the harness reads the setting back rather than assuming
the `SET` took effect:

```
transaction_read_only = on; statement_timeout = 5000 ms; role = docray_api; principal = 0260a042-9b61-41b0-86e7-3ce8f38aa2dc
```

Every statement is additionally screened before execution; a non-read statement
raises rather than reaching the server (`eval_phrase_retrieval.guard_sql`).

## Reproducing this report

```
python scripts/eval_phrase_retrieval.py                # isolated container leg
python scripts/eval_phrase_retrieval.py --prod         # production, read-only
```
