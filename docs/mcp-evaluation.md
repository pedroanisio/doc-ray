---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "Claude Opus 4.8 (1M context) via Claude Code"
  date: "2026-07-25"
---

# MCP evaluation harness

[← back to README](../README.md) · [MCP service](mcp-service.md) · [architecture](architecture.md)

## Disclaimer

This work is subject to the methodological caveats and commitments described in [@DISCLAIMER.md](../DISCLAIMER.md).
> No statement or premise not backed by a real logical definition or verifiable reference should be taken for granted.

## Why this exists

Anthropic's *Writing effective tools for AI agents* closes on one recommendation:
tool surfaces are tuned **empirically, not by intuition**. Until this harness, the
doc-ray MCP surface had none of that — items 1–3 *claimed* to reduce agent
friction with no measurement, item 4 *claimed* a 37% payload reduction that was a
character count of one synthetic page, and items 5 and 6 had their design premises
falsified only by hand, late. Every change to the surface was unfalsifiable.

That is the defect this harness closes, and it is the programme-level application
of **PALS's Law**: the outputs being consumed here are model behaviours, and they
are unverified by default. The harness makes a change to a tool description or
return shape **show** that it helped or hurt, rather than merely asserting it.

## What it measures

The harness runs a fixed set of grounded retrieval tasks against a seeded, throw-away
fixture corpus and reports, per task and in aggregate:

- **pass/fail** against scoring predicates that encode the intended behaviour,
- **tool-call count**,
- **total response characters** (exact) and an **estimated token count**
  (`chars ÷ 4`, a *documented estimate*, never a measurement — see Open decisions),
- **within-budget** (a call/character ceiling grounded in the failure it guards),
- **wall-clock** (recorded, but excluded from every deterministic metric).

It measures the **agent-facing surface**, not the GraphQL API or Postgres, and not
model quality. It reports numbers; it does not conclude which model is better.

## How it works

- **It calls the real tool callables** (`corpus`/`structure`/`compose`) exactly as
  an agent would — the functions under test already carry token-forwarding, typed
  errors, and RLS. The tools forward a GraphQL query over `httpx`; the harness has
  no API server, so it routes that POST to the Strawberry schema executed
  **in-process** against the test database (see `application/mcp_eval.py::tool_transport`).
  The real error-mapping path in `corpus.gql` still runs — that is where the
  cursor-steering hint is produced, so the harness measures the true error shape.
- **Fixtures are disposable and self-created** (`application/mcp_eval_fixtures.py`).
  The harness refuses to run against any database that holds non-fixture documents,
  and destroys everything it seeds. It never writes to a corpus it did not create.
- **Scoring predicates are pure** functions over the recorded transcript
  (`domain/mcp_eval_scoring.py`), so they are unit-testable with no database and
  produce identical numbers for identical input.

### Two execution modes (FR-5)

| Mode | What it measures | Status |
|---|---|---|
| `scripted` (default) | the **surface**: payload sizes, error shapes, whether the data an agent needs is reachable in N calls. The harness *is* the scripted agent, executing the intended-correct strategy, so a regression that breaks the surface shows up as a failed assertion. Deterministic, free, CI-safe. | **implemented** |
| `agentic` | **behaviour**: a real model drives the tools. | **not implemented in this build** — no model credentials are provisioned here (Open decision #2). `--mode agentic` fails with a typed `EvalModeUnsupported` naming exactly what is missing; it is never a silent no-op. |

### Two task tiers

- **grounded** (6, FR-2) — each derived from a failure this programme actually
  observed, scored by a behavioural predicate.
- **coverage** (8) — reachability smoke tasks that exercise every *other* registered
  read tool at least once. A coverage task asserts the tool ran and returned the
  documented shape, **even when the fixture legitimately yields an empty result**
  (a document with no images) — honest coverage of the validation/GraphQL/shaping
  path, not a behavioural claim. The [drift guard](../src/doc_ray/tests/test_mcp_eval_tasks.py)
  fails the build if any registered read tool is exercised by no task.

## Running it

From the repo root, against the Docker test stack (so `DATABASE_URL` points at a
disposable test database):

```bash
# deterministic surface run, per-task table + aggregate to stdout
python scripts/eval_mcp.py --mode scripted

# also write the machine-readable report
python scripts/eval_mcp.py --mode scripted --json docs/eval/mcp-baseline.json

# compare against the committed baseline; exits non-zero on regression
python scripts/eval_mcp.py --mode scripted --baseline docs/eval/mcp-baseline.json

# log every tool call (tool, args, bytes, duration)
python scripts/eval_mcp.py --mode scripted --verbose
```

The canonical way to run it (fixtures need Postgres) is inside the test stack — the
same image the suite uses:

```bash
TEST_PYTEST_ARGS="src/doc_ray/tests/test_mcp_eval.py src/doc_ray/tests/test_mcp_eval_tasks.py -q" \
  ./scripts/test-in-docker.sh
```

**Exit codes:** `0` ok · `1` baseline regression · `2` a task failed · `3` mode
unsupported.

## Reading the report

The table is one row per task plus an aggregate line (the committed baseline):

```
task                                     result calls   chars   ~tok  budget
------------------------------------------------------------------------------
cover-document-exhaustively              PASS       3   16955   4239      ok
find-rare-scene                          PASS       1     821    205      ok
survive-keyword-flood                    PASS       2    9600   2400      ok
read-the-ending                          PASS       2    8264   2066      ok
entity-roster-not-fooled-by-type-split   PASS       1     403    101      ok
cursor-not-interchangeable               PASS       3    3261    815      ok
coverage-document-discovery              PASS       3    1147    287      ok
coverage-collections-and-lenses          PASS       3    2033    508      ok
coverage-corpus-map                      PASS       2     794    198      ok
coverage-entities-and-lexemes            PASS       3     224     56      ok
coverage-job-status                      PASS       1     331     83      ok
coverage-visual-surface                  PASS       4       8      2      ok
coverage-composition-grounding           PASS       3    5798   1450      ok
coverage-semantic-search                 PASS       1       2      0      ok
------------------------------------------------------------------------------
AGGREGATE (scripted)                     14/14     32   49641  12410
```

The JSON report (`--json`) is the machine-readable form: `mode`, a `tasks` array
(each with `task_id`, `grounded_in`, `passed`, `calls`, `chars`, `est_tokens`,
`within_budget`, `budget`, and per-`assertions` detail), an `aggregate` block, and
a run-level `wall_clock_seconds`. **Only `wall_clock_seconds` is non-deterministic**
— strip it to compare two runs:

```bash
python - <<'PY'
import json
a=json.load(open("run1.json")); b=json.load(open("run2.json"))
a.pop("wall_clock_seconds",None); b.pop("wall_clock_seconds",None)
print("identical" if a==b else "DIFFERENT")
PY
```

## The task set

| id | tier | guards |
|---|---|---|
| `cover-document-exhaustively` | grounded | the reviewer ran out of budget on the back half of a novel |
| `find-rare-scene` | grounded | the rare-term `search_sentences` guidance (Item 1) |
| `survive-keyword-flood` | grounded | a flooded region reading as *missing content* (BL-145) |
| `read-the-ending` | grounded | addressing the final region instead of paging from 0 (Item 5) |
| `entity-roster-not-fooled-by-type-split` | grounded | one contested entity reported as four (Item 6) |
| `cursor-not-interchangeable` | grounded | a `search_sentences` cursor handed to the reader (BL-145) |
| `coverage-document-discovery` | coverage | `full_text_search` / `list_documents` / `get_document` |
| `coverage-collections-and-lenses` | coverage | `list_collections` / `get_collection` / `list_lenses` |
| `coverage-corpus-map` | coverage | `corpus_manifest` / `corpus_shard` |
| `coverage-entities-and-lexemes` | coverage | `entity_frequency` / `lexeme_kwic` / `entity_mention_corrections` |
| `coverage-job-status` | coverage | `get_job` |
| `coverage-visual-surface` | coverage | the four Prism visual read tools |
| `coverage-composition-grounding` | coverage | `briefing_pack` / `entity_dossier` / `document_evidence` |
| `coverage-semantic-search` | coverage | `semantic_search_sentences` (Layer 20; a documented `unsupported` outcome counts as reachable) |

## Adding a task

A task is a declarative record in [`application/mcp_eval_tasks.py`](../src/doc_ray/application/mcp_eval_tasks.py),
never a script. Add a `Step` sequence and pure `Assertion`s, then register it in
`TASKS`; the drift guard forces a task for any new read tool:

```python
_MY_TASK = EvalTask(
    id="find-the-index",
    kind="grounded",                      # or "coverage"
    prompt="Where is the index of this book?",
    grounded_in="agents paged from ordinal 0 instead of using the outline",
    steps=(
        Step(structure.read_document_outline, lambda ctx: {"document_id": ctx.ids["doc_main"]}),
        Step(
            corpus.read_document_sentences,
            lambda ctx: {"document_id": ctx.ids["doc_main"], "from_ordinal": 35, "to_ordinal": 39},
        ),
    ),
    assertions=(
        Assertion("addresses_ending", lambda calls: scoring.check_addresses_ending(calls, 0.15)),
        _within(TaskBudget(max_calls=3, max_chars=10_000)),
    ),
    budget=TaskBudget(max_calls=3, max_chars=10_000),
)
```

If a task needs a fixture the world does not provide (a document with no headings,
a type-split entity), seed it in
[`application/mcp_eval_fixtures.py`](../src/doc_ray/application/mcp_eval_fixtures.py)
and expose it in the returned ids — never against production data. If you write a
new scoring predicate, put it in
[`domain/mcp_eval_scoring.py`](../src/doc_ray/domain/mcp_eval_scoring.py) with a
unit test that exercises a passing *and* a failing transcript, red-first.

## Baseline and comparison

A committed baseline lives at [`docs/eval/mcp-baseline.json`](eval/mcp-baseline.json),
produced by an actual `scripted` run against the Docker test stack (PostgreSQL 16 /
`pgvector:pg16`, the same image the suite uses; `mode: scripted`; the non-deterministic
`wall_clock_seconds` is stripped from the committed file). Regenerate it with
`--json docs/eval/mcp-baseline.json`. `--baseline <path>` compares a fresh run
against it and exits non-zero on any of: a task that passed in the baseline now
failing, a call-count increase beyond `--call-tolerance` (default 0), or a payload
growth beyond `--char-tolerance` (default 10%).

## Open decisions (recorded per the issue)

1. **Token counting.** Characters are counted exactly; tokens are reported as a
   labelled estimate at `chars ÷ 4` (`domain/mcp_eval_scoring.py::CHARS_PER_TOKEN`).
   doc-ray avoids an unnecessary tokenizer dependency, and a stated estimate is
   honest where a precise-looking wrong number is not. The estimate is never
   reported as a measurement.
2. **Agentic mode** ships as a mode switch with a typed `unsupported` error, not an
   implementation: no model credentials are provisioned in this environment.
   Implementing it means wiring a model client and registering its key in
   `config/env.registry.yaml`.
3. **CI gating.** The harness is wired so it *can* run in CI, but merges are **not**
   gated on it initially — the baseline is published and the comparison command is
   available; an operator decides when the numbers are trusted enough to gate.

## Files

| Path | Role |
|---|---|
| `scripts/eval_mcp.py` | CLI runner |
| `src/doc_ray/application/mcp_eval.py` | runner, in-process transport, report assembly |
| `src/doc_ray/application/mcp_eval_tasks.py` | the task registry |
| `src/doc_ray/application/mcp_eval_fixtures.py` | disposable fixture world |
| `src/doc_ray/domain/mcp_eval_scoring.py` | pure scoring predicates + serialization |
| `src/doc_ray/tests/test_mcp_eval.py` | unit + regression + integration tests |
| `src/doc_ray/tests/test_mcp_eval_tasks.py` | drift guard (every read tool exercised) |
| `docs/eval/mcp-baseline.json` | committed baseline |
