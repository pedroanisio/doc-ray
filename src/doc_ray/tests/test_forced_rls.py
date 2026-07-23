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

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "db" / "migrations" / "106_force_rls.sql"


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
