---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Fable 5 via Claude Code"
  date: "2026-07-25"
---

# capacity_model — analytical PostgreSQL capacity model

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

A quantitative model — not a load generator — of how the doc-ray PostgreSQL
database is expected to behave at **~10,000 users and ~1,000,000 documents**.
It chains five explicit, auditable layers:

> application behavior → operation patterns → relational model and cardinality
> → PostgreSQL resource consumption → expected performance and failure thresholds

## Run

From the repo root (no database, no network — pure computation):

```bash
python scripts/run_capacity_model.py                  # full run + sensitivity
python scripts/run_capacity_model.py --no-sensitivity # faster
python scripts/run_capacity_model.py --hardware scaled
python scripts/run_capacity_model.py --list-assumptions
python scripts/run_capacity_model.py --config overrides.json
```

Outputs (gitignored): `out/capacity_model/capacity_report.md` (human) and
`capacity_report.json` (machine-readable). An overrides file is a JSON object
of `{assumption-name: value}`; names come from `--list-assumptions`.

## What it computes

1. Table/index/TOAST sizes at target scale, from the documented page format
   and repo-measured cardinality ratios, calibrated against the repo's own
   ~215 MB-per-annotated-document measurement.
2. QPS/TPS, WAL, and commit rates under four scenarios (baseline / expected /
   peak / degraded).
3. Connection/concurrency needs vs the deployed pool topology.
4. Per-operation latency via M/M/c queueing over CPU and disk stations.
5. Bottlenecks, saturation multipliers, and capacity margins.
6. Cardinality/distribution effects on plans (RLS InitPlan scale, statistics
   targets vs skew, keyset vs OFFSET, ULID vs uuid4 locality).
7. Autovacuum cadence/debt, dead tuples, WAL/FPI, checkpoint pressure.
8. A halve/double sensitivity ranking of every opted-in assumption.
9. An explicit split between analytical conclusions and those requiring
   empirical validation.

## Where the inputs come from

Every input is an `Assumption` with a value, unit, source class
(`repo` / `postgresql-docs` / `literature` / `derived` / `assumed`), citation
(`path:line` or URL), rationale, and epistemic tag. Repo-derived facts include
the migration-034 autovacuum overrides, the psycopg pool sizes, the measured
fact-table row counts in `engineering_decisions.yml`, and the RLS design of
migrations 018/106/110. Where the repo is silent (document-length mix at 1M
docs, cloud-disk latency), the model uses explicit conservative assumptions —
never hidden defaults — and the sensitivity pass reports which of them move
the results.

Module map is in [`__init__.py`](./__init__.py); tests live in
[`tests/test_capacity_model.py`](../../tests/test_capacity_model.py) and
[`tests/test_capacity_model_primitives.py`](../../tests/test_capacity_model_primitives.py).

Back to the repo overview: [root README](../../README.md) · tooling index:
[scripts/README.md](../README.md)
