"""NFR-3 — the production leg must be structurally incapable of writing.

Issue #47 runs part of its measurement against the live corpus. `ENVIRONMENT.md`
is explicit that on this host every command is live and that recovery from a bad
*write* depends on backups the codebase does not take. "The harness only issues
SELECTs" is therefore not good enough as a promise: it has to be an enforced
property, and the enforcement has to be visible in the report as evidence.

These tests drive that enforcement through a recording double, so they hold
without touching any database.
"""

from __future__ import annotations

import _phrase_eval_criteria as crit
import eval_phrase_retrieval as harness
import pytest


class _Cursor:
    def __init__(self, log: list[str], rows: list[tuple] | None = None) -> None:
        self._log = log
        self._rows = rows if rows is not None else [("on",)]

    def execute(self, sql, params=None):  # noqa: ANN001 - test double
        self._log.append(str(sql))
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.log: list[str] = []
        self._rows = rows

    def cursor(self, *args, **kwargs):  # noqa: ANN002, ANN003 - test double
        return _Cursor(self.log, self._rows)

    def execute(self, sql, params=None):  # noqa: ANN001 - test double
        self.log.append(str(sql))
        return _Cursor(self.log, self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- the session is read-only and time-boxed before any query runs -----------


def test_the_measurement_session_is_set_read_only():
    conn = _Conn()

    harness.prepare_session(conn, statement_timeout_ms=5000)

    assert any("READ ONLY" in sql.upper() for sql in conn.log)


def test_the_measurement_session_sets_a_defensive_statement_timeout():
    """NFR-3 names 5 s: an unbounded scan on production is itself the hazard."""
    conn = _Conn()

    harness.prepare_session(conn, statement_timeout_ms=5000)

    assert any("statement_timeout" in sql for sql in conn.log)
    assert any("5000" in sql for sql in conn.log)


def test_preparing_a_session_verifies_the_setting_actually_took():
    """Issuing SET is not proof; the harness reads the value back as evidence."""
    conn = _Conn(rows=[("on",)])

    evidence = harness.prepare_session(conn, statement_timeout_ms=5000)

    assert "transaction_read_only" in evidence
    assert "on" in evidence


def test_a_session_that_did_not_go_read_only_aborts_the_run():
    """If the server reports `off`, the harness must refuse to proceed."""
    conn = _Conn(rows=[("off",)])

    with pytest.raises(harness.UnsafeSessionError):
        harness.prepare_session(conn, statement_timeout_ms=5000)


# --- the measured principal decides what RLS lets the benchmark see ----------


def test_a_supplied_principal_is_applied_to_the_session():
    """RLS filters by `app.principal_id`; the benchmark must say who it reads as.

    The principal is not a detail: it decides how many rows the candidate scan
    has to consider, and therefore what the latency means.
    """
    conn = _Conn(rows=[("on",)])

    harness.prepare_session(conn, statement_timeout_ms=5000, principal_id="p-1")

    assert any("app.principal_id" in sql for sql in conn.log)


def test_no_principal_leaves_the_session_unscoped():
    conn = _Conn(rows=[("on",)])

    harness.prepare_session(conn, statement_timeout_ms=5000)

    assert not any("app.principal_id" in sql for sql in conn.log)


def test_a_supplied_role_is_assumed_before_measuring():
    """`app.principal_id` is only honoured for the API's own database role.

    `docray_current_principal()` resolves the actor by `db_role` first and only
    falls back to the GUC for `docray_api`. Measuring as the owning role would
    therefore ignore the principal entirely and benchmark a different query.
    """
    conn = _Conn(rows=[("on",)])

    harness.prepare_session(conn, statement_timeout_ms=5000, role="docray_api")

    assert any(sql.upper().startswith("SET ROLE") for sql in conn.log)


def test_the_evidence_records_the_assumed_role():
    conn = _Conn(rows=[("on",)])

    evidence = harness.prepare_session(conn, statement_timeout_ms=5000, role="docray_api")

    assert "docray_api" in evidence


@pytest.mark.parametrize(
    "role",
    ["docray_api; DROP TABLE sentences", "docray api", "docray-api", "1role", ""],
)
def test_an_unsafe_role_name_is_refused(role: str):
    """`SET ROLE` cannot be parameterized, so the identifier is validated."""
    conn = _Conn(rows=[("on",)])

    if role == "":
        harness.prepare_session(conn, statement_timeout_ms=5000, role=role)
        assert not any(sql.upper().startswith("SET ROLE") for sql in conn.log)
    else:
        with pytest.raises(ValueError):
            harness.prepare_session(conn, statement_timeout_ms=5000, role=role)


def test_the_evidence_records_which_principal_was_measured():
    conn = _Conn(rows=[("on",)])

    evidence = harness.prepare_session(conn, statement_timeout_ms=5000, principal_id="p-1")

    assert "p-1" in evidence


# --- the harness must not benchmark rows it cannot see -----------------------


def test_a_session_that_can_see_no_sentences_aborts_the_run():
    """RLS is FORCEd on `sentences` (migration 106).

    A role with no readable documents measures an empty result set in
    microseconds and would publish that as a benchmark. An empty corpus is a
    measurement failure, not a fast query.
    """
    conn = _Conn(rows=[])

    with pytest.raises(harness.EmptyCorpusError):
        harness.assert_corpus_visible(conn)


def test_corpus_visibility_is_probed_with_a_bounded_query():
    """A `count(*)` over the sentences fact table is exactly what D-024 forbids."""
    conn = _Conn(rows=[(1,)])

    harness.assert_corpus_visible(conn)

    assert any("limit 1" in sql.lower() for sql in conn.log)
    assert not any("count(" in sql.lower() for sql in conn.log)


# --- which access path the planner actually chose ----------------------------


def test_a_sequential_scan_plan_is_reported_as_using_no_index():
    """`Seq Scan` over the fact table is the finding, not a detail.

    A latency number alone cannot tell an operator whether the fix is a new
    engine or an index the database already has, so the harness records the
    access path beside the timing.
    """
    plan = (
        "Limit  (cost=12.75..97584.03 rows=1 width=162)\n"
        "  ->  Seq Scan on sentences s  (cost=12.75..97584.03 rows=1 width=162)\n"
        "        Filter: (text ~~* '%Model%'::text)"
    )

    assert harness.scan_kind(plan) == "Seq Scan"
    assert harness.uses_an_index(plan) is False


@pytest.mark.parametrize(
    "node",
    ["Bitmap Index Scan on idx_sentences_text_trgm", "Index Scan using idx_x on sentences"],
)
def test_an_index_plan_is_reported_as_using_one(node: str):
    assert harness.uses_an_index(f"Limit\n  ->  {node}") is True


def test_a_plan_that_could_not_be_captured_is_not_claimed_either_way():
    """A timed-out EXPLAIN proves nothing about the access path."""
    assert harness.scan_kind("EXPLAIN (ANALYZE) did not complete within 5000 ms.") is None
    assert harness.uses_an_index("EXPLAIN (ANALYZE) did not complete within 5000 ms.") is None


def test_a_timed_out_analyze_does_not_hide_the_captured_access_path():
    """The transcript holds both halves: the plan always, the timings sometimes.

    A class too slow to execute under the bound is exactly the class whose
    access path matters most, so a failed ANALYZE must not erase the plain
    EXPLAIN that did succeed.
    """
    transcript = (
        "Limit  (cost=12.75..97584.03 rows=1 width=162)\n"
        "  ->  Seq Scan on sentences s  (cost=12.75..97584.03 rows=1 width=162)\n"
        "\n-- with ANALYZE --\n"
        "EXPLAIN (ANALYZE) did not complete within 5000 ms."
    )

    assert harness.scan_kind(transcript) == "Seq Scan"
    assert harness.uses_an_index(transcript) is False


# --- catalog estimates: unknown is not zero ----------------------------------


def test_an_unanalyzed_table_reports_unknown_rather_than_zero():
    """PostgreSQL uses reltuples = -1 for "never analyzed", not "empty".

    Treating it as 0 would price the stage-2 index at zero bytes — a fabricated
    number dressed as a measurement.
    """
    assert harness.normalize_reltuples(-1.0) is None


def test_a_measured_reltuples_is_returned_as_a_row_count():
    assert harness.normalize_reltuples(25_000_000.0) == 25_000_000


def test_zero_reltuples_is_a_real_measurement_not_unknown():
    assert harness.normalize_reltuples(0.0) == 0


# --- every statement is screened before it reaches the server ----------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM sentences LIMIT 1",
        "select count(*) from (select 1) t",
        "EXPLAIN (ANALYZE, BUFFERS) SELECT 1",
        "  WITH x AS (SELECT 1) SELECT * FROM x  ",
    ],
)
def test_read_statements_pass_the_screen(sql: str):
    assert harness.guard_sql(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE sentences SET text = 'x'",
        "DELETE FROM sentences",
        "INSERT INTO sentences (id) VALUES (1)",
        "DROP TABLE sentences",
        "TRUNCATE sentences",
        "ALTER TABLE sentences ADD COLUMN x int",
        "CREATE INDEX ON sentences (id)",
        "SELECT 1; DROP TABLE sentences",
        "REFRESH MATERIALIZED VIEW m",
        "GRANT SELECT ON sentences TO public",
    ],
)
def test_write_statements_are_refused_before_execution(sql: str):
    with pytest.raises(harness.WriteAttemptError):
        harness.guard_sql(sql)


def test_an_explain_without_analyze_is_still_a_read():
    assert harness.guard_sql("EXPLAIN SELECT 1")


def test_explain_analyze_of_a_write_is_refused():
    """`EXPLAIN ANALYZE UPDATE ...` actually executes the update."""
    with pytest.raises(harness.WriteAttemptError):
        harness.guard_sql("EXPLAIN (ANALYZE) UPDATE sentences SET text = 'x'")


# --- a timeout must not silently strip the session context -------------------


class _TimeoutOnceConn(_Conn):
    """Raises the server's cancel exactly once, like a real statement timeout."""

    def __init__(self, rows: list[tuple] | None = None) -> None:
        super().__init__(rows)
        self.raised = False
        self.rollbacks = 0

    def execute(self, sql, params=None):  # noqa: ANN001 - test double
        import psycopg

        self.log.append(str(sql))
        if not self.raised and "ILIKE" in str(sql):
            self.raised = True
            raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
        return _Cursor(self.log, self._rows)

    def rollback(self):
        self.rollbacks += 1


def test_a_timed_out_class_restores_the_session_before_the_next_one():
    """`SET ROLE` and `set_config` are transactional in PostgreSQL.

    Rolling back the aborted transaction also discards them, so every later
    class would run unscoped — RLS would hide the corpus and the remaining
    classes would report fast scans over zero rows. That is the exact shape of a
    plausible-but-worthless benchmark.
    """
    conn = _TimeoutOnceConn(rows=[])
    restored: list[int] = []

    measurement, _ = harness.measure_class(
        conn,
        crit.QUERY_CLASSES[0],
        runs=1,
        timeout_ms=5000,
        restore_session=lambda: restored.append(1),
    )

    assert measurement.timed_out is True
    assert conn.rollbacks == 1
    assert restored == [1], "the session context was not re-established after the rollback"


# --- the banner tells the operator what is about to happen, without secrets --


def test_the_prod_banner_names_the_rules_of_engagement():
    text = harness.prod_banner("postgresql://user:s3cr3t@db.example.com:25060/defaultdb")

    assert "read-only" in text.lower()
    assert "production" in text.lower()


def test_the_banner_never_prints_the_credential():
    """NFR-3: connection string from the environment, never logged."""
    text = harness.prod_banner("postgresql://user:s3cr3t@db.example.com:25060/defaultdb")

    assert "s3cr3t" not in text
    assert "db.example.com" in text


def test_the_banner_redacts_a_url_it_cannot_parse():
    text = harness.prod_banner("not a url at all s3cr3t")

    assert "s3cr3t" not in text


# --- CLI contract ------------------------------------------------------------


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        harness.main(["--help"])

    assert exc.value.code == 0
    assert "--prod" in capsys.readouterr().out


def test_the_default_leg_is_not_production():
    args = harness.parse_args([])

    assert args.prod is False


def test_the_default_sample_size_is_the_od2_recommendation():
    args = harness.parse_args([])

    assert args.runs == 20
