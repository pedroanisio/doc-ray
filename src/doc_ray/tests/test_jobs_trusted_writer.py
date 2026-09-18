"""The jobs queue is trusted infrastructure, not a user-writable table (BL-141).

A non-admin ``member`` (the self-registered user the ownership model was built
for, migrations 102/104) must be able to upload a document. An upload enqueues a
``KIND_INGEST`` job whose ``document_id`` is NULL — the document does not exist
until the worker creates it — so it hit the old ``jobs`` INSERT policy ``p_ins``,
which allowed a document-less job only for admins. The result on production was::

    new row violates row-level security policy for table "jobs"

for every member upload.

The fix follows how mature systems treat an async queue: authorize the request at
the API boundary (the mutation guards already do this — ``_require_write_audited``
for ingest, ``_require_document_write`` for document-scoped work, ``_require_admin``
for corpus-wide jobs), then enqueue as a *trusted writer*. The queue table is not
a user-facing resource: RLS on ``jobs`` governs READ visibility only, and the
INSERT runs as ``docray_batch`` (the same identity the worker uses), which the
policies exempt. ``enqueue_async`` elevates to that role ``LOCAL`` to a
transaction, so it never leaks back into the request's RLS-bound reads.

These tests run against the live schema and the REAL ``jobs`` table as
``docray_api`` with a member principal — the production path. They are skipped
without the isolated test DB, like every other DB-backed suite.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from pathlib import Path

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row
from starlette.datastructures import Headers, UploadFile

from doc_ray.adapters import queue_postgres as queue
from doc_ray.config import Config
from doc_ray.domain.jobs import KIND_INGEST
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal, apply_principal

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION = _ROOT / "db" / "migrations" / "113_jobs_trusted_writer_enqueue.sql"


def _member(conn) -> str:
    """Create a fresh non-admin member principal; return its id."""
    member_id = str(uuid.uuid4())
    _itdb.ensure_member_principal(conn, member_id, f"member-{member_id}")
    return member_id


# --------------------------------------------------------------------------
# Migration: the jobs INSERT gate is gone; visibility stays.
# --------------------------------------------------------------------------


def test_migration_113_drops_the_insert_gate_and_keeps_read_visibility() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    # The user-facing write gate is gone: enqueue moves to the trusted writer.
    assert "DROP POLICY IF EXISTS p_ins ON jobs" in sql
    # Read visibility is preserved and gains an owner branch, so a member can see
    # the document-less job they enqueued without seeing anyone else's.
    assert "CREATE POLICY p_read ON jobs" in sql
    assert "enqueued_by = docray_current_principal()" in sql
    assert "trusted" in sql.lower()


def test_jobs_has_no_user_facing_insert_policy(pg) -> None:
    """After 111, no INSERT policy exists: with forced RLS that denies every
    non-bypass role, which is the whole point — only the batch writer enqueues."""
    conn, _created = pg
    ins_policies = [
        row["policyname"]
        for row in conn.execute(
            "SELECT policyname FROM pg_policies WHERE tablename = 'jobs' AND cmd = 'INSERT'"
        ).fetchall()
    ]
    assert ins_policies == [], f"jobs must have no INSERT policy, found: {ins_policies}"
    # But read visibility must remain.
    read_policies = [
        row["policyname"]
        for row in conn.execute(
            "SELECT policyname FROM pg_policies WHERE tablename = 'jobs' AND cmd = 'SELECT'"
        ).fetchall()
    ]
    assert read_policies, "jobs must keep a SELECT (read-visibility) policy"


# --------------------------------------------------------------------------
# The queue is trusted-writer-only: a user role cannot enqueue directly.
# --------------------------------------------------------------------------


def test_docray_api_cannot_insert_a_job_directly(pg) -> None:
    """A user role (even an admin principal) must not write the queue directly;
    enqueue is infrastructure. This is the boundary the trusted writer enforces."""
    conn, _created = pg
    admin_id = str(uuid.uuid4())
    _itdb.ensure_admin_principal(conn, admin_id, f"admin-{admin_id}")
    conn.execute("SET ROLE docray_api")
    conn.execute("SELECT set_config('app.principal_id', %s, false)", (admin_id,))
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO jobs (kind, payload, document_id) VALUES ('ingest', '{}'::jsonb, NULL)"
            )
    finally:
        conn.execute("RESET ROLE")
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM principals WHERE id = %s", (admin_id,))


def test_the_batch_writer_enqueues_a_document_less_ingest_job(pg) -> None:
    """The trusted writer inserts the exact row a member upload produces."""
    conn, _created = pg
    conn.execute("SET ROLE docray_batch")
    try:
        job_id = queue.enqueue(conn, KIND_INGEST, {"storage_key": "aa/aa.txt"}, document_id=None)
        row = conn.execute("SELECT kind, document_id FROM jobs WHERE id = %s", (job_id,)).fetchone()
        assert row is not None and row["kind"] == "ingest" and row["document_id"] is None
    finally:
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


# --------------------------------------------------------------------------
# enqueue_async elevates to the trusted writer, then restores the role.
# --------------------------------------------------------------------------


def test_enqueue_async_lets_a_member_enqueue_and_restores_the_role(pg) -> None:
    """The core regression, at the adapter boundary.

    A member connection bound to ``docray_api`` calls ``enqueue_async`` for a
    document-less ingest job. It must succeed (the trusted-writer elevation), and
    afterwards the connection must be back on ``docray_api`` so the request's
    subsequent RLS-bound reads are still filtered — the elevation is LOCAL, not a
    leak.
    """
    conn, _created = pg
    member_id = _member(conn)

    async def check() -> None:
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            await apply_principal(
                aconn,
                Principal(subject="m", principal_id=member_id, scopes=frozenset({"read", "write"})),
            )
            job_id = await queue.enqueue_async(
                aconn, KIND_INGEST, {"storage_key": "bb/bb.txt"}, document_id=None
            )
            # The role reverted after the LOCAL elevation: still docray_api.
            role = (await (await aconn.execute("SELECT current_role AS r")).fetchone())["r"]
            assert role == "docray_api", f"role leaked as {role!r}, must revert to docray_api"
            await aconn.execute("RESET ROLE")
            got = await (
                await aconn.execute("SELECT kind FROM jobs WHERE id = %s", (job_id,))
            ).fetchone()
            assert got is not None and got["kind"] == "ingest"
            await aconn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        finally:
            await aconn.close()

    asyncio.run(check())


def test_sync_enqueue_lets_an_rls_bound_role_enqueue_and_restores_the_role(pg) -> None:
    """The synchronous twin must elevate identically — the operator-CLI path.

    The defect this pins: BL-141 moved
    ``enqueue_async`` onto the trusted writer but left the sync ``enqueue``
    inserting under whatever role it was handed. Every operator tool that goes
    through it — ``scripts/enqueue_visual_backfill.py`` above all, which is the
    documented way to run a corpus-wide Prism backfill — died with

        new row violates row-level security policy for table "jobs"

    against the managed cluster, because the login role there (``docray``) is
    RLS-bound and ``jobs`` has no INSERT policy at all. The role must revert
    after the LOCAL elevation for the same reason as the async path.
    """
    conn, _created = pg
    member_id = _member(conn)
    conn.execute("SET ROLE docray_api")
    conn.execute("SELECT set_config('app.principal_id', %s, false)", (member_id,))
    try:
        job_id = queue.enqueue(conn, KIND_INGEST, {"storage_key": "cc/cc.txt"}, document_id=None)
        role = conn.execute("SELECT current_role AS r").fetchone()["r"]
        assert role == "docray_api", f"role leaked as {role!r}, must revert to docray_api"
    finally:
        conn.execute("RESET ROLE")
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


# --------------------------------------------------------------------------
# Read visibility is unchanged: a member sees their own document's jobs only.
# --------------------------------------------------------------------------


def test_member_sees_jobs_for_owned_documents_only(pg) -> None:
    conn, created = pg
    conn.execute("SET ROLE docray_batch")
    owner_id = _member(conn)
    other_id = _member(conn)
    owned = _itdb.make_doc(conn, "Owned by member.")
    created.append(owned)
    other = _itdb.make_doc(conn, "Owned by someone else.")
    created.append(other)
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner_id, owned))
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (other_id, other))
    owned_job = queue.enqueue(conn, "annotate", {}, document_id=owned)
    other_job = queue.enqueue(conn, "annotate", {}, document_id=other)
    try:
        conn.execute("SET ROLE docray_api")
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (owner_id,))
        visible = {
            row["id"]
            for row in conn.execute(
                "SELECT id FROM jobs WHERE id = ANY(%s)", ([owned_job, other_job],)
            ).fetchall()
        }
        assert owned_job in visible, "a member must see jobs for a document they own"
        assert other_job not in visible, "a member must not see another owner's document's jobs"
    finally:
        conn.execute("RESET ROLE")
        conn.execute("SET ROLE docray_batch")
        conn.execute("DELETE FROM jobs WHERE id = ANY(%s)", ([owned_job, other_job],))


def test_member_enqueues_a_document_scoped_job_on_an_owned_document(pg) -> None:
    """Document-scoped enqueues (reannotate/delete/...) also route through the
    trusted writer now that ``p_ins`` is gone. A member acting as ``docray_api``
    must be able to enqueue a job for a document they own, and see it afterwards.
    """
    conn, created = pg
    conn.execute("SET ROLE docray_batch")
    owner_id = _member(conn)
    doc_id = _itdb.make_doc(conn, "Owned, reannotated by its owner.")
    created.append(doc_id)
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner_id, doc_id))

    async def check() -> None:
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        job_id: str | None = None
        try:
            await apply_principal(
                aconn,
                Principal(subject="m", principal_id=owner_id, scopes=frozenset({"read", "write"})),
            )
            job_id = await queue.enqueue_async(
                aconn, "reannotate", {"lang": "en"}, document_id=doc_id, enqueued_by=owner_id
            )
            # Visible to the owner via the document-readable branch of p_read.
            got = await (
                await aconn.execute("SELECT document_id FROM jobs WHERE id = %s", (job_id,))
            ).fetchone()
            assert got is not None and str(got["document_id"]) == doc_id
        finally:
            if job_id is not None:
                await aconn.execute("RESET ROLE")
                await aconn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
            await aconn.close()

    asyncio.run(check())


# --------------------------------------------------------------------------
# End-to-end: a member's ingestDocument mutation enqueues on the REAL queue.
# --------------------------------------------------------------------------


def test_member_ingest_document_mutation_enqueues_under_forced_rls(pg) -> None:
    """The production failure, reproduced through the GraphQL mutation.

    Unlike the existing ingest tests (which shadow ``jobs`` with a TEMP TABLE and
    run as the superuser test role, bypassing RLS), this drives the real
    ``ingestDocument`` resolver on a connection bound to ``docray_api`` with a
    non-admin member principal, writing the REAL ``jobs`` table — exactly the path
    the webapp uses. Before the fix it raised the RLS violation shown on prod.
    """
    try:  # the blob PUT is real — skip when MinIO is down, like the sibling tests
        from doc_ray.adapters.object_store import ObjectStore

        ObjectStore(Config.from_env()).ensure_bucket()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"object store unreachable: {exc}")

    conn, _created = pg
    member_id = _member(conn)
    schema = build_schema()
    upload = UploadFile(
        io.BytesIO(b"Member upload under forced RLS."),
        filename="member-upload.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def check() -> None:
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        member = Principal(subject="m", principal_id=member_id, scopes=frozenset({"read", "write"}))
        job_id: str | None = None
        try:
            await apply_principal(aconn, member)
            ctx = RequestContext(conn=aconn, principal=member, loaders=Loaders(aconn))
            r = await schema.execute(
                'mutation($f: Upload!){ ingestDocument(file: $f, title: "member") '
                "{ id kind status } }",
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert r.errors is None, r.errors
            assert r.data["ingestDocument"]["kind"] == "ingest"
            assert r.data["ingestDocument"]["status"] == "queued"
            job_id = r.data["ingestDocument"]["id"]
            # The ingest job records the uploader so the worker can own the doc.
            await aconn.execute("RESET ROLE")
            payload = (
                await (
                    await aconn.execute("SELECT payload FROM jobs WHERE id = %s", (job_id,))
                ).fetchone()
            )["payload"]
            assert payload["owner_principal_id"] == member_id
        finally:
            if job_id is not None:
                await aconn.execute("RESET ROLE")
                await aconn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
            await aconn.close()

    asyncio.run(check())
