"""FORCE ROW LEVEL SECURITY: the database, not the app tier, enforces isolation (BL-138).

Every corpus table had ``ENABLE ROW LEVEL SECURITY`` but none had ``FORCE``, and
both the API and the webapp connect as ``docray`` — which *owns* those tables.
Postgres exempts a table owner from its own policies unless the table is forced,
so any code path that failed to ``SET ROLE`` read the corpus with no policy
applied at all. RLS that the application tier can silently opt out of is a
convention, not an access-control boundary.

Migration 106 closes that by making the bypass explicit instead of incidental:
the trusted batch writer (worker, CLIs) drops to ``docray_batch``, which the
policies recognise, and every RLS table is forced so the owner role no longer
walks through. A path that forgets to ``SET ROLE`` now fails closed.

The RLS tests run against the live schema (skipped without the isolated test
DB, like every other DB-backed suite).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "db" / "migrations" / "106_force_rls.sql"

# A SECURITY DEFINER function that reads a forced-RLS table must be owned by a
# BYPASSRLS role, or its body re-enters the table's policy and recurses to a
# stack-depth crash — but only for a non-admin caller and only when the owner
# is not a superuser, which is why the managed-Postgres outage (BL-142) never
# reproduced on the superuser-owned test DB. This query is the invariant, shared
# by migration 114's guard.
#: SECURITY DEFINER functions whose OWNER cannot EXECUTE a `docray_*` helper their
#: body names. The body runs as the owner, so this is a guaranteed runtime failure
#: for every caller — not a caller-specific permissions issue, which is why it
#: survived review. Matching is by name against `pg_get_functiondef`, the same
#: approximation the BYPASSRLS guard below uses: it can over-report a helper merely
#: mentioned in a comment, and that is the safe direction to err.
_SECDEF_CALLEES_OWNER_CANNOT_EXECUTE = r"""
    SELECT f.oid::regprocedure::text AS caller,
           fo.rolname                AS caller_runs_as,
           g.oid::regprocedure::text AS uncallable_callee
      FROM pg_proc f
      JOIN pg_roles fo ON fo.oid = f.proowner
      JOIN pg_proc g  ON g.pronamespace = 'public'::regnamespace
                     AND g.oid <> f.oid
                     AND g.proname LIKE 'docray%'
                     AND pg_get_functiondef(f.oid) ~ ('\y' || g.proname || '\y')
     WHERE f.prosecdef
       AND f.pronamespace = 'public'::regnamespace
       AND NOT has_function_privilege(fo.rolname, g.oid, 'EXECUTE')
     ORDER BY 1, 3
"""

_NON_BYPASS_SECDEF_HELPERS = """
    SELECT p.oid::regprocedure::text AS sig, r.rolname AS owner
      FROM pg_proc p
      JOIN pg_roles r ON r.oid = p.proowner
     WHERE p.prosecdef
       AND p.pronamespace = 'public'::regnamespace
       AND NOT r.rolbypassrls
       AND EXISTS (
            SELECT 1 FROM pg_class c
             WHERE c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity
               AND c.relnamespace = 'public'::regnamespace
               AND pg_get_functiondef(p.oid) ~ ('\\y' || c.relname || '\\y'))
"""


def test_migration_106_forces_rls_and_refuses_to_run_without_the_batch_writer() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "docray_batch" in migration
    assert "BYPASSRLS" in migration
    # Forcing RLS without a working batch writer stops every ingest and
    # annotation job, so a half-applied state must not be reachable.
    assert "RAISE EXCEPTION" in migration


def test_the_batch_role_carries_bypassrls(pg) -> None:
    """The exemption is a named role attribute, not incidental table ownership."""
    conn, _created = pg
    _reapply(conn)
    row = conn.execute(
        "SELECT rolbypassrls FROM pg_roles WHERE rolname = 'docray_batch'"
    ).fetchone()
    assert row is not None, "the batch-writer role must exist"
    assert row["rolbypassrls"], "the batch writer's exemption must be explicit"


def _reapply(conn) -> None:
    """Re-apply 106 as the session user, then return to the batch identity.

    The migration grants ``docray_batch`` membership, which ``docray_batch``
    itself may not do — and the fixture connection is already in that role.
    """
    conn.execute("RESET ROLE")
    conn.execute(_MIGRATION.read_text(encoding="utf-8"))
    conn.execute("SET ROLE docray_batch")


def test_every_rls_enabled_table_is_forced(pg) -> None:
    """No table may enable RLS without forcing it — that is the whole defect."""
    conn, _created = pg
    _reapply(conn)
    unforced = [
        row["relname"]
        for row in conn.execute(
            "SELECT relname FROM pg_class "
            "WHERE relrowsecurity AND NOT relforcerowsecurity AND relkind = 'r' "
            "ORDER BY relname"
        ).fetchall()
    ]
    assert not unforced, f"RLS enabled but not forced: {unforced}"


def test_a_privileged_non_superuser_role_cannot_read_without_a_principal(pg) -> None:
    """The bypass under test, modelled faithfully.

    In production the app connects as ``docray``: full table privileges, owner of
    the corpus tables, and **not** a superuser (a superuser bypasses RLS no matter
    what, which is why the deployment must never connect as one). This creates an
    equivalent role — every privilege, no bypass attribute — and asserts it sees
    nothing without a principal. The isolated test DB's own user IS a superuser,
    so asserting against it directly would prove nothing.
    """
    import _itdb

    conn, created = pg
    _reapply(conn)
    doc_id = _itdb.make_doc(conn, "Forced-RLS probe.")
    created.append(doc_id)
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"forced-owner-{uuid.uuid4()}",),
    ).fetchone()
    owner = str(row["id"])
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    probe = f"probe_app_{uuid.uuid4().hex[:12]}"
    conn.execute("RESET ROLE")
    conn.execute(f"CREATE ROLE {probe} NOLOGIN NOSUPERUSER NOBYPASSRLS")
    conn.execute(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {probe}")
    conn.execute(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {probe}")
    try:
        conn.execute(f"SET ROLE {probe}")
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        visible = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
        assert visible is None, (
            "a privileged app role with no principal must read nothing once RLS is forced"
        )
    finally:
        conn.execute("RESET ROLE")
        conn.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {probe}")
        conn.execute(f"REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM {probe}")
        conn.execute(f"DROP ROLE IF EXISTS {probe}")
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id = %s", (owner,))


def test_the_batch_writer_may_still_read_and_write_the_whole_corpus(pg) -> None:
    """The worker's bypass becomes explicit rather than incidental.

    Ingest, annotation, and the rollup refreshes span every tenant and run with
    no request principal, so the batch writer must remain able to work — but by
    holding a role the policies name, not by happening to own the tables.
    """
    import _itdb

    conn, created = pg
    _reapply(conn)
    doc_id = _itdb.make_doc(conn, "Batch writer probe.")
    created.append(doc_id)
    conn.execute("UPDATE documents SET owner_principal_id = NULL WHERE id = %s", (doc_id,))
    conn.execute("SET ROLE docray_batch")
    try:
        assert (
            conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone() is not None
        ), "the batch writer must read regardless of ownership"
        conn.execute("UPDATE documents SET description = 'batched' WHERE id = %s", (doc_id,))
        assert (
            conn.execute("SELECT description FROM documents WHERE id = %s", (doc_id,)).fetchone()[
                "description"
            ]
            == "batched"
        )
    finally:
        conn.execute("RESET ROLE")
        conn.execute("SET ROLE docray_batch")


def test_the_batch_writer_may_refresh_the_tenant_rollups(pg) -> None:
    """corpus_stats and friends carry a SELECT-only policy — no write policy exists.

    These rollups are refreshed by the trusted writer at the pipeline job
    boundary (D-024). They have a SELECT policy and no write policy at all, so
    forcing RLS without a batch-writer branch would silently stop every refresh.
    """
    conn, _created = pg
    _reapply(conn)
    tenant = conn.execute("SELECT docray_default_tenant_id() AS id").fetchone()["id"]
    try:
        conn.execute(
            "INSERT INTO corpus_stats (tenant_id, n_distinct_lexemes) VALUES (%s, 1) "
            "ON CONFLICT (tenant_id) DO UPDATE SET n_distinct_lexemes = EXCLUDED.n_distinct_lexemes",
            (tenant,),
        )
        row = conn.execute(
            "SELECT n_distinct_lexemes FROM corpus_stats WHERE tenant_id = %s", (tenant,)
        ).fetchone()
        assert row is not None and row["n_distinct_lexemes"] == 1
    finally:
        conn.execute("RESET ROLE")
        conn.execute("SET ROLE docray_batch")


def test_no_security_definer_helper_that_reads_a_forced_rls_table_lacks_bypass(pg) -> None:
    """Schema invariant (BL-142): every SECURITY DEFINER helper reading a forced-RLS
    table is BYPASSRLS-owned. This is the guard that was missing when the org-corpus
    helpers (migrations 108/110) shipped owned by the non-superuser `docray`, which
    on managed Postgres recursed to a stack-depth 500 on every non-admin corpus read.
    Migration 114 makes it true; this keeps it true as new helpers are added.
    """
    conn, _created = pg
    conn.execute("RESET ROLE")
    offenders = conn.execute(_NON_BYPASS_SECDEF_HELPERS).fetchall()
    conn.execute("SET ROLE docray_batch")
    assert offenders == [], (
        "SECURITY DEFINER helpers that read a forced-RLS table must be owned by a "
        f"BYPASSRLS role or they recurse under FORCE RLS: {[dict(r) for r in offenders]}"
    )


def test_every_secdef_function_can_execute_the_helpers_it_calls(pg) -> None:
    """A SECURITY DEFINER function must be able to call what its body names.

    SECURITY DEFINER switches identity to the function's OWNER *before* the body
    runs, so the privileges that matter are the owner's, not the caller's. When a
    helper is created owned by one role, has its default grants revoked, and is
    then called from a definer function owned by a different role, every call
    fails — for every caller, including the role that owns the helper. That is
    what makes it hard to see: it does not look like a permissions problem from
    any particular caller's point of view.

    Migration 154 shipped exactly that. It created

        docray_refresh_document_lexeme_stats(uuid)
        docray_refresh_document_entity_frequency_stats(uuid)
        docray_unapply_document_rollups(uuid)

    owned by `docray`, revoked ALL from PUBLIC, and never granted EXECUTE to
    `docray_batch` — the owner of their only caller,
    `docray_refresh_stats_for_document`. Every per-document rollup refresh then
    failed with `permission denied for function
    docray_refresh_document_lexeme_stats`, on every document, from the moment the
    migration applied. It was logged per document rather than raised, so ingestion
    continued while `lexeme_stats`, `entity_frequency_stats` and the `corpus_stats`
    totals derived from them silently stopped tracking the corpus. Migration 159
    grants the missing EXECUTE; this keeps it true as new helpers are added.
    """
    conn, _created = pg
    conn.execute("RESET ROLE")
    offenders = conn.execute(_SECDEF_CALLEES_OWNER_CANNOT_EXECUTE).fetchall()
    conn.execute("SET ROLE docray_batch")
    assert offenders == [], (
        "a SECURITY DEFINER function names a helper its OWNER may not EXECUTE, so "
        "every call through it fails at runtime regardless of caller: "
        f"{[dict(r) for r in offenders]}"
    )


def test_a_secdef_policy_helper_without_bypass_recurses_and_bypass_fixes_it(pg) -> None:
    """The outage mechanism, reproduced self-contained on the test DB.

    The superuser-owned test DB cannot trigger the bug naturally (its owner
    bypasses RLS), so this builds the managed condition from scratch: a forced-RLS
    table whose SELECT policy calls a SECURITY DEFINER helper that reads the same
    table. Owned by a NON-BYPASSRLS role the read recurses (stack depth); handing
    the helper a BYPASSRLS owner — migration 114's fix — breaks the loop.
    """
    conn, _created = pg
    conn.execute("RESET ROLE")
    tag = uuid.uuid4().hex[:10]
    probe, tbl, fn = f"probe_{tag}", f"rls_probe_{tag}", f"readable_{tag}"
    try:
        conn.execute(f"CREATE ROLE {probe} NOLOGIN NOBYPASSRLS")
        conn.execute(f"GRANT CREATE, USAGE ON SCHEMA public TO {probe}")
        conn.execute(f"CREATE TABLE {tbl} (id int PRIMARY KEY)")
        conn.execute(f"INSERT INTO {tbl} VALUES (1)")
        conn.execute(f"GRANT SELECT ON {tbl} TO {probe}, docray_api, docray_batch")
        conn.execute(
            f"CREATE FUNCTION {fn}() RETURNS SETOF int LANGUAGE sql STABLE "
            f"SECURITY DEFINER AS 'SELECT id FROM {tbl}'"
        )
        conn.execute(f"GRANT EXECUTE ON FUNCTION {fn}() TO docray_api")
        conn.execute(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        conn.execute(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        conn.execute(f"CREATE POLICY p ON {tbl} FOR SELECT USING (id IN (SELECT {fn}()))")

        # 1) Non-BYPASSRLS owner → the helper's own read re-enters the policy → recursion.
        conn.execute(f"ALTER FUNCTION {fn}() OWNER TO {probe}")
        conn.execute("SET ROLE docray_api")
        with pytest.raises(psycopg.errors.StatementTooComplex):  # "stack depth limit exceeded"
            conn.execute(f"SELECT id FROM {tbl}").fetchall()
        conn.execute("RESET ROLE")

        # 2) BYPASSRLS owner (the fix) → the helper's read skips the policy → no loop.
        conn.execute(f"ALTER FUNCTION {fn}() OWNER TO docray_batch")
        conn.execute("SET ROLE docray_api")
        assert conn.execute(f"SELECT id FROM {tbl}").fetchall() == [{"id": 1}]
    finally:
        conn.execute("RESET ROLE")
        conn.execute(f"DROP TABLE IF EXISTS {tbl} CASCADE")
        conn.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
        conn.execute(f"REVOKE ALL ON SCHEMA public FROM {probe}")
        conn.execute(f"DROP ROLE IF EXISTS {probe}")
        conn.execute("SET ROLE docray_batch")
