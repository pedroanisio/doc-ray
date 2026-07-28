#!/usr/bin/env python
"""Phrase-retrieval evaluation harness (issue #47, Retrieval · P8).

ARCHITECTURAL REQUIREMENT (PALS's LAW): LLMs will always produce some form of error.
Absence of output verification is a design defect, not a runtime bug.
This harness exists so the #48 go/no-go is decided from measurements rather than
from an agent's confidence about them.

Measures the shipped stage-1 phrase path (#45 search, #46 counts) for
correctness and latency, prices the stage-2 positional index from catalog
numbers, and renders a dated report whose every figure carries provenance.

Usage (from the repo root)::

    python scripts/eval_phrase_retrieval.py                     # isolated container leg
    python scripts/eval_phrase_retrieval.py --prod              # production, read-only
    python scripts/eval_phrase_retrieval.py --report docs/eval/phrase-retrieval-<date>.md

**The production leg never writes.** It runs inside a read-only transaction with
a defensive statement timeout, reads the setting back as evidence, and screens
every statement before execution — see `ENVIRONMENT.md` rules of engagement.

The decision logic lives in ``_phrase_eval_criteria`` and the rendering in
``_phrase_eval_report`` so each file stays inside the D-015 net-LOC cap.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Callable, Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _phrase_eval_criteria as crit  # noqa: E402  (after sys.path bootstrap)
import _phrase_eval_report as report  # noqa: E402

DEFAULT_STATEMENT_TIMEOUT_MS = 5000
#: Shared candidate cap of both surfaces under test
#: (term_frequency.SENTENCE_HIGH_HITS / persistence_sentence_search.PHRASE_CANDIDATE_SCAN_CAP).
CANDIDATE_CAP = 2001

_EXIT_OK, _EXIT_UNSAFE, _EXIT_NO_DB = 0, 1, 2


class UnsafeSessionError(RuntimeError):
    """The session did not actually become read-only."""


class WriteAttemptError(RuntimeError):
    """A statement that is not a pure read was about to be executed."""


class EmptyCorpusError(RuntimeError):
    """The measuring session can see no sentences, so there is nothing to time."""


def assert_corpus_visible(conn) -> None:
    """Refuse to benchmark rows the session cannot read.

    RLS is FORCEd on ``sentences`` (migration 106). A role with no readable
    documents returns the empty set in microseconds, and those microseconds
    would otherwise be published as a latency result. Probed with a bounded
    query — a ``count(*)`` here is the very shape D-024 exists to forbid.
    """
    row = conn.execute(guard_sql("SELECT 1 FROM sentences LIMIT 1")).fetchone()
    if not row:
        raise EmptyCorpusError(
            "this session can see no sentences (RLS, or an empty corpus); "
            "refusing to report latencies measured over an empty result set"
        )


def normalize_reltuples(value: float) -> int | None:
    """Map ``pg_class.reltuples`` to a row count, or ``None`` for unknown.

    PostgreSQL stores ``-1`` for a relation that has never been vacuumed or
    analyzed, meaning *unknown* — not *empty*. Rounding that to zero would price
    the stage-2 index at zero bytes and present it as a measurement.
    """
    if value < 0:
        return None
    return int(value)


#: Leading keyword of every statement the harness is allowed to issue.
_READ_PREFIXES = ("select", "with", "explain", "show", "table", "values")
#: Keywords that make a statement a write however it is wrapped.
_WRITE_TOKENS = re.compile(
    r"\b(insert|update|delete|truncate|drop|alter|create|grant|revoke|"
    r"refresh|reindex|vacuum|analyze\s+\w|copy|call|do|set\s+role)\b",
    re.IGNORECASE,
)


def guard_sql(sql: str) -> str:
    """Return *sql* unchanged, or raise if it is not a pure read.

    Belt and braces alongside the read-only transaction: the transaction is the
    server-side guarantee, this is the one that fires before anything leaves the
    process, so a mistake is visible in a traceback rather than in a rollback.
    """
    text = sql.strip()
    if ";" in text.rstrip(";"):
        raise WriteAttemptError(f"multiple statements are not allowed: {sql!r}")
    lowered = text.lower()
    if not lowered.startswith(_READ_PREFIXES):
        raise WriteAttemptError(f"not a read statement: {sql!r}")
    body = lowered
    if body.startswith("explain"):
        # EXPLAIN ANALYZE actually executes its target, so screen the target too.
        body = re.sub(r"^explain\s*(\([^)]*\))?\s*", "", body)
        if not body.startswith(_READ_PREFIXES):
            raise WriteAttemptError(f"EXPLAIN of a non-read statement: {sql!r}")
    if _WRITE_TOKENS.search(body):
        raise WriteAttemptError(f"write keyword in statement: {sql!r}")
    return sql


#: `SET ROLE` takes an identifier, not a parameter, so the name is validated
#: rather than escaped.
_SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def prepare_session(
    conn, *, statement_timeout_ms: int, principal_id: str = "", role: str = ""
) -> str:
    """Make the session read-only and time-boxed, then verify it took.

    ``role`` is the database role to measure as. It matters because
    ``docray_current_principal()`` resolves the actor from ``db_role`` first and
    only falls back to the ``app.principal_id`` GUC for ``docray_api``: measured
    as any other role, the principal is ignored and RLS hides the corpus. Only
    de-escalation is useful here — the harness never needs more rights than the
    API has.

    ``principal_id`` then scopes the session for row-level security. It is
    recorded in the evidence because it decides how many rows the candidate scan
    sees, and therefore what every latency in the report means.

    Returns the evidence string quoted in the report (NFR-3).
    """
    conn.execute(f"SET statement_timeout = {int(statement_timeout_ms)}")
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET TRANSACTION READ ONLY")
    if role:
        if not _SAFE_IDENTIFIER.match(role):
            raise ValueError(f"unsafe role identifier: {role!r}")
        conn.execute(f"SET ROLE {role}")
    if principal_id:
        # set_config is a session setting, not a data write: legal inside a
        # read-only transaction and required to read anything under RLS.
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
    row = conn.execute("SHOW transaction_read_only").fetchone()
    value = str(row[0]) if row else "unknown"
    if value.lower() not in {"on", "true"}:
        raise UnsafeSessionError(
            f"session reports transaction_read_only={value!r}; refusing to measure"
        )
    scope = principal_id or "unscoped (no RLS principal)"
    return (
        f"transaction_read_only = {value}; statement_timeout = {statement_timeout_ms} ms; "
        f"role = {role or 'connection default'}; principal = {scope}"
    )


def prod_banner(database_url: str) -> str:
    """The rules-of-engagement banner — host and database only, never secrets."""
    label = "an unparseable DATABASE_URL"
    try:
        parsed = urllib.parse.urlsplit(database_url)
        if parsed.hostname:
            label = parsed.hostname
            if parsed.path and parsed.path != "/":
                label = f"{label}{parsed.path}"
    except ValueError:
        pass
    return (
        "=" * 72 + "\n"
        "PRODUCTION LEG — this is a live corpus (ENVIRONMENT.md rules of engagement).\n"
        f"Target: {label}\n"
        "Mode:   read-only transaction, statement timeout enforced, every\n"
        "        statement screened; no write is reachable from this harness.\n" + "=" * 72
    )


def _redacted(database_url: str) -> str:
    return prod_banner(database_url).splitlines()[2].removeprefix("Target: ")


# --- measurement legs --------------------------------------------------------


def _time_call(fn, *args, **kwargs) -> float:
    started = time.perf_counter()
    fn(*args, **kwargs)
    return (time.perf_counter() - started) * 1000.0


def measure_class(
    conn,
    qc: crit.QueryClass,
    *,
    runs: int,
    timeout_ms: int,
    restore_session: Callable[[], object] | None = None,
):
    """Measure one query class over the *shipped* candidate stage.

    Runs the same parameterized candidate clause the P6/P7 adapters build
    (:func:`phrase_candidate_clause`) under the same 2001-row cap, which is the
    stage that dominates their cost. Verification is then the shipped
    ``surface_word_v1`` matcher, so the correctness column measures the released
    profile rather than a re-implementation.

    A statement timeout is recorded as a measurement — a floor at the timeout —
    because "the scan could not finish" is precisely the result FR-5 needs.
    """
    import psycopg

    from doc_ray.adapters.sentence_search_sql import phrase_candidate_clause
    from doc_ray.domain.phrase_profile import find_occurrences

    predicate, params = phrase_candidate_clause("s.text", qc.phrase)
    sql = guard_sql(f"SELECT s.id, s.text FROM sentences s WHERE {predicate} LIMIT {CANDIDATE_CAP}")

    samples: list[float] = []
    candidates: list[tuple] = []
    timed_out = False

    def _run() -> None:
        nonlocal candidates
        candidates = conn.execute(sql, params).fetchall()

    try:
        _run()  # warm the cache; OD-2 measures warm latency
        for _ in range(runs):
            samples.append(_time_call(_run))
    except psycopg.errors.QueryCanceled:
        # The transaction is aborted by the cancel; roll back so the remaining
        # classes still measure. This is a rollback of reads, not of writes.
        #
        # `SET ROLE` and `set_config` are transactional in PostgreSQL, so the
        # rollback also discards the measured role and RLS principal. Without
        # restoring them every later class would run unscoped, and RLS would
        # quietly reduce it to a fast scan over zero rows.
        timed_out = True
        samples = [float(timeout_ms)]
        conn.rollback()
        if restore_session is not None:
            restore_session()

    texts = [row[1] for row in candidates]
    occurrence_sentences = sum(1 for t in texts if find_occurrences(t, qc.phrase))
    false_positives = _boundary_false_positives(texts, qc.phrase)

    return crit.ClassMeasurement(
        key=qc.key,
        p50_ms=crit.percentile(samples, 50),
        p95_ms=crit.percentile(samples, 95),
        runs=runs,
        capped=len(candidates) >= CANDIDATE_CAP,
        false_positives=false_positives,
        agreement=None if (timed_out or len(candidates) >= CANDIDATE_CAP) else True,
        candidates=len(candidates),
        timed_out=timed_out,
    ), occurrence_sentences


def _boundary_false_positives(texts: Sequence[str], phrase: str) -> int:
    """Candidates the profile rejects are *expected*, not false positives.

    FR-1's "zero word-boundary false positives in phrase mode" is about what the
    surface *returns*. A verified hit whose match is not on word boundaries would
    be the defect; the candidate superset containing near-misses is by design.
    """
    from doc_ray.domain.phrase_profile import find_occurrences, word_spans

    bad = 0
    for text in texts:
        boundaries = {start for start, _ in word_spans(text)}
        ends = {end for _, end in word_spans(text)}
        for start, end in find_occurrences(text, phrase):
            if start not in boundaries or end not in ends:
                bad += 1
    return bad


def catalog_numbers(conn) -> dict[str, float | None]:
    """FR-4 inputs: catalog estimates and a bounded sample — never a full scan.

    Unknown values stay ``None`` all the way to the report rather than
    collapsing into a zero that reads like a measurement.
    """
    rows = conn.execute(
        guard_sql(
            "SELECT relname, reltuples FROM pg_class "
            "WHERE relname IN ('tokens', 'sentences', 'lexeme_occurrences')"
        )
    ).fetchall()
    out: dict[str, float | None] = {
        str(name): normalize_reltuples(float(count)) for name, count in rows
    }
    sample = conn.execute(
        guard_sql(
            "SELECT avg(length(text)), count(DISTINCT lower(text)) "
            "FROM sentences TABLESAMPLE SYSTEM (1)"
        )
    ).fetchone()
    out["avg_sentence_chars"] = float(sample[0]) if sample and sample[0] is not None else None
    out["sampled_distinct"] = float(sample[1]) if sample and sample[1] is not None else None
    return out


#: Plan nodes that read a relation, in the order we prefer to report them.
_SCAN_NODES = (
    "Bitmap Index Scan",
    "Index Only Scan",
    "Index Scan",
    "Bitmap Heap Scan",
    "Seq Scan",
)


#: Separates the always-available plain EXPLAIN from the ANALYZE run that may
#: have timed out. Only the first half describes the chosen access path.
ANALYZE_MARKER = "-- with ANALYZE --"


def scan_kind(plan: str) -> str | None:
    """The access path the planner chose, or ``None`` if the plan is unavailable.

    Reported alongside the timings because a millisecond figure cannot tell an
    operator *why* it is slow. A `Seq Scan` over the sentences fact table points
    at candidate generation; an index scan that is still slow would point
    somewhere else entirely.

    Only the plain-EXPLAIN half is inspected: a class too slow to execute under
    the bound is precisely the one whose access path matters most, so a failed
    ANALYZE must not erase the plan that was captured successfully.
    """
    chosen = plan.split(ANALYZE_MARKER, 1)[0]
    if "did not complete" in chosen:
        return None
    for node in _SCAN_NODES:
        if node in chosen:
            return node
    return None


def uses_an_index(plan: str) -> bool | None:
    """Whether the chosen plan reads through any index. ``None`` if unknown."""
    kind = scan_kind(plan)
    if kind is None:
        return None
    return kind != "Seq Scan"


def _safe_plans(conn, *, timeout_ms: int, restore_session: Callable[[], object]) -> list:
    """Capture a plan per class, recording the timeout instead of aborting the run.

    ``EXPLAIN ANALYZE`` executes its target, so a class that cannot finish inside
    the defensive bound cannot yield a plan either. That absence is itself a
    result and is written into the transcript rather than left blank. The session
    context is restored after each cancel for the same reason as in
    :func:`measure_class`.
    """
    import psycopg

    out: list[tuple[str, str]] = []
    for qc in crit.QUERY_CLASSES:
        # A plain EXPLAIN only plans, so it always returns and always yields the
        # chosen access path — the finding that survives even when the class is
        # too slow to execute under the bound.
        chosen = explain_class(conn, qc, analyze=False)[1]
        try:
            measured = explain_class(conn, qc, analyze=True)[1]
        except psycopg.errors.QueryCanceled:
            conn.rollback()
            restore_session()
            measured = f"EXPLAIN (ANALYZE) did not complete within {timeout_ms} ms."
        out.append((qc.key, f"{chosen}\n\n{ANALYZE_MARKER}\n{measured}"))
    return out


def explain_class(conn, qc: crit.QueryClass, *, analyze: bool = True) -> tuple[str, str]:
    """FR-3: one plan per class, off the request path."""
    from doc_ray.adapters.sentence_search_sql import phrase_candidate_clause

    predicate, params = phrase_candidate_clause("s.text", qc.phrase)
    options = "(ANALYZE, BUFFERS) " if analyze else ""
    sql = guard_sql(
        f"EXPLAIN {options}"
        f"SELECT s.id, s.text FROM sentences s WHERE {predicate} LIMIT {CANDIDATE_CAP}"
    )
    lines = [str(row[0]) for row in conn.execute(sql, params).fetchall()]
    return qc.key, "\n".join(lines)


# --- CLI ---------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eval_phrase_retrieval.py",
        description="Phrase-retrieval evaluation harness (issue #47, P8). Read-only.",
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="measure against the live corpus (read-only transaction, screened statements)",
    )
    parser.add_argument(
        "--runs", type=int, default=crit.DEFAULT_RUNS, help="warm runs per class (OD-2)"
    )
    parser.add_argument("--report", metavar="PATH", help="write the dated Markdown report here")
    parser.add_argument(
        "--explain", action="store_true", help="capture EXPLAIN (ANALYZE, BUFFERS) per class"
    )
    parser.add_argument(
        "--lemma-requirement",
        action="store_true",
        help="operator has stated a product requirement for lemma-phrase search (FR-5)",
    )
    parser.add_argument(
        "--principal",
        default="",
        metavar="UUID",
        help="RLS principal to measure as; without it a scoped role sees no rows",
    )
    parser.add_argument(
        "--role",
        default="",
        metavar="NAME",
        help="database role to assume (use docray_api to measure the API's own read path)",
    )
    parser.add_argument(
        "--date", default="", help="report date (defaults to today, UTC) — pinned for determinism"
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL is not set; nothing to measure.", file=sys.stderr)
        return _EXIT_NO_DB
    if args.prod:
        print(prod_banner(database_url))

    import psycopg

    with psycopg.connect(database_url) as conn:

        def _establish() -> str:
            return prepare_session(
                conn,
                statement_timeout_ms=DEFAULT_STATEMENT_TIMEOUT_MS,
                principal_id=args.principal,
                role=args.role,
            )

        evidence = _establish()
        print(evidence)
        assert_corpus_visible(conn)

        server: list[crit.ClassMeasurement] = []
        for qc in crit.QUERY_CLASSES:
            measurement, verified = measure_class(
                conn,
                qc,
                runs=args.runs,
                timeout_ms=DEFAULT_STATEMENT_TIMEOUT_MS,
                restore_session=_establish,
            )
            server.append(measurement)
            flags = "  [capped]" if measurement.capped else ""
            flags += "  [TIMED OUT]" if measurement.timed_out else ""
            print(
                f"{qc.key:<22} p50={measurement.p50_ms:8.2f} ms  "
                f"p95={measurement.p95_ms:8.2f} ms  candidates={measurement.candidates:<5} "
                f"verified_sentences={verified}{flags}"
            )

        plans = (
            _safe_plans(conn, timeout_ms=DEFAULT_STATEMENT_TIMEOUT_MS, restore_session=_establish)
            if args.explain
            else []
        )
        catalog = catalog_numbers(conn)

    tokens, sentences = catalog.get("tokens"), catalog.get("sentences")
    sampled, avg_chars = catalog.get("sampled_distinct"), catalog.get("avg_sentence_chars")
    price = None
    if None in (tokens, sentences, sampled, avg_chars):
        print(
            "catalog estimates unavailable (a relation has never been analyzed); "
            "stage-2 pricing will be reported as not measured",
            file=sys.stderr,
        )
    else:
        price = crit.price_stage_two(
            token_rows=int(tokens or 0),
            sentence_rows=int(sentences or 0),
            # TABLESAMPLE SYSTEM (1) reads ~1 % of pages, so the sampled
            # distinct count is scaled back up. Extrapolation, hence estimate.
            distinct_surfaces=int((sampled or 0) * 100),
            avg_sentence_chars=float(avg_chars or 0.0),
        )
    decision = crit.decide(server, lemma_requirement=args.lemma_requirement)
    print(f"\nVERDICT: {decision.verdict}\n{decision.rationale}")

    run = report.EvalRun(
        generated_on=args.date or _today(),
        leg="production (read-only)" if args.prod else "isolated container",
        database_label=_redacted(database_url),
        server_measurements=server,
        client_measurements=[],
        decision=decision,
        price=price,
        catalog_provenance="pg_class.reltuples + TABLESAMPLE SYSTEM (1), this harness",
        plans=plans,
        read_only_evidence=evidence,
    )
    if args.report:
        text = report.render_report(run)
        bare = report.find_unprovenanced_numbers(text)
        if bare:
            print(f"refusing to write a report with unprovenanced numbers: {bare}", file=sys.stderr)
            return _EXIT_UNSAFE
        Path(args.report).write_text(text, encoding="utf-8")
        print(f"wrote {args.report}")
    return _EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
