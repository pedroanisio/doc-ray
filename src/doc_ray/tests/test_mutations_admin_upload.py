"""Admin-gated and upload GraphQL mutations (backup, cancel, ingestDocument).

Split out of test_mutations.py (D-015); shares _mutations_common.py fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest
from _mutations_common import WRITE_PRINCIPAL, _aconn, _aconn_as, _make_principal

from doc_ray.adapters import persistence as db
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal


def test_create_backup_requires_admin_not_just_write_scope(pg):
    """create_backup/restore_backup are corpus-wide — a non-admin write token must be refused."""
    conn, created = pg
    non_admin = _make_principal(conn, display_name=f"non-admin-{new_ulid_str()}")
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(non_admin))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="non-admin",
                    principal_id=str(non_admin),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation { createBackup(note: "x") { id } }',
                context_value=ctx,
            )
            assert result.errors is not None
            assert "admin" in str(result.errors[0]).lower()
        finally:
            await aconn.close()

    asyncio.run(check())
    conn.execute("DELETE FROM principals WHERE id = %s", (non_admin,))


# ---------------------------------------------------------------------------
# BL-098 — ingestDocument had no upload-size cap at all (unlike the webapp's
# Flask route, which enforces DOCRAY_MAX_UPLOAD_MB); a client could buffer an
# arbitrarily large upload into API process memory before any validation ran.
# ---------------------------------------------------------------------------


def test_ingest_document_rejects_oversized_upload(pg, monkeypatch):
    """ingestDocument must enforce the same upload-size ceiling the webapp does."""
    import io

    from starlette.datastructures import Headers, UploadFile

    monkeypatch.setenv("DOCRAY_MAX_UPLOAD_MB", "1")
    schema = build_schema()
    body = b"x" * (2 * 1024 * 1024)  # 2 MiB, comfortably over the 1 MiB cap
    upload = UploadFile(
        io.BytesIO(body),
        filename="big-upload.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def check():
        # Shadow the real queue: the size check must reject the upload before
        # any store.put/enqueue is attempted, but a shadow table is cheap
        # insurance against that assumption regressing silently.
        aconn = await _aconn()
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation($f: Upload!){ ingestDocument(file: $f) { id } }",
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert result.errors is not None
            assert "exceeds" in str(result.errors[0]).lower()
            cur = await aconn.execute("SELECT count(*) AS n FROM jobs")
            assert (await cur.fetchone())["n"] == 0
        finally:
            await aconn.close()

    asyncio.run(check())


def test_ingest_document_accepts_upload_within_the_cap(pg, monkeypatch):
    """A normal-sized upload is unaffected by the new bounded-read path."""
    import io

    from starlette.datastructures import Headers, UploadFile

    from doc_ray.adapters.object_store import ObjectStore
    from doc_ray.config import Config

    try:  # skip when MinIO is not running — the blob PUT is real
        ObjectStore(Config.from_env()).ensure_bucket()
    except Exception as exc:
        pytest.skip(f"object store unreachable: {exc}")

    monkeypatch.setenv("DOCRAY_MAX_UPLOAD_MB", "1")
    schema = build_schema()
    body = b"Well within the cap."
    upload = UploadFile(
        io.BytesIO(body),
        filename="small-upload.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def check():
        aconn = await _aconn()
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation($f: Upload!){ ingestDocument(file: $f, pipeline: false) "
                "{ kind status } }",
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["ingestDocument"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())


def test_ingest_document_payload_carries_the_uploader(pg, monkeypatch):
    """The enqueued job must name the uploader (migration 102).

    Ownership can only be resolved here: by the time the worker runs there is no
    request principal to consult. If the payload loses this field the upload
    still succeeds, but the document lands unowned — invisible to the very user
    who uploaded it — so the wiring is asserted rather than assumed.
    """
    import io

    from starlette.datastructures import Headers, UploadFile

    from doc_ray.adapters.object_store import ObjectStore
    from doc_ray.config import Config

    try:  # skip when MinIO is not running — the blob PUT is real
        ObjectStore(Config.from_env()).ensure_bucket()
    except Exception as exc:
        pytest.skip(f"object store unreachable: {exc}")

    conn, _created = pg
    # A real principal row, not the shared WRITE_PRINCIPAL constant: the mutation
    # writes an audit event whose actor is a FK into principals.
    uploader = _make_principal(conn, display_name=f"uploader-{new_ulid_str()}")
    schema = build_schema()
    upload = UploadFile(
        io.BytesIO(b"Ownership is stamped at the API boundary."),
        filename="owned-upload.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def check():
        aconn = await _aconn_as(str(uploader))
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="uploader",
                    principal_id=str(uploader),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                "mutation($f: Upload!){ ingestDocument(file: $f, pipeline: false) { status } }",
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            row = await (await aconn.execute("SELECT payload FROM jobs")).fetchone()
            assert row is not None, "ingestDocument must enqueue a job"
            assert row["payload"]["owner_principal_id"] == str(uploader)
        finally:
            await aconn.close()

    asyncio.run(check())
    conn.execute("DELETE FROM principals WHERE id = %s", (uploader,))


# ---------------------------------------------------------------------------
# BL-106 — the upload extension is client-controlled; it must be checked
# against extraction.py's format allowlist before the blob is ever persisted
# to the object store, not after.
# ---------------------------------------------------------------------------


def test_ingest_document_rejects_unsupported_extension_before_storing_blob(pg, monkeypatch):
    import io

    from starlette.datastructures import Headers, UploadFile

    from doc_ray.adapters.object_store import ObjectStore

    put_calls = []
    monkeypatch.setattr(ObjectStore, "put", lambda self, *a, **k: put_calls.append(a))
    schema = build_schema()
    upload = UploadFile(
        io.BytesIO(b"MZ\x90\x00fake-binary"),
        filename="totally-legit.exe",
        headers=Headers({"content-type": "application/octet-stream"}),
    )

    async def check():
        aconn = await _aconn()
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation($f: Upload!){ ingestDocument(file: $f) { id } }",
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert result.errors is not None
            assert "unsupported" in str(result.errors[0]).lower()
            cur = await aconn.execute("SELECT count(*) AS n FROM jobs")
            assert (await cur.fetchone())["n"] == 0
        finally:
            await aconn.close()

    asyncio.run(check())
    assert not put_calls, "the blob must never reach the object store"


# ---------------------------------------------------------------------------
# BL-095 — a duplicate upload is stored by the API before the worker can
# short-circuit on content_sha256. That pre-worker PUT must preserve the
# format-derived Content-Type instead of downgrading existing object metadata
# to application/octet-stream.
# ---------------------------------------------------------------------------


def test_duplicate_upload_preserves_existing_object_content_type(pg, monkeypatch):
    import io

    from starlette.datastructures import Headers, UploadFile

    from doc_ray.adapters.object_store import ObjectStore

    conn, created = pg
    body = b"Already stored document."
    sha = hashlib.sha256(body).hexdigest()
    key = f"{sha[:2]}/{sha}.txt"
    doc_id = db.insert_document(
        conn,
        title="Already stored",
        doc_type="txt",
        mime_type="text/plain",
        original_filename="already-stored.txt",
        source_uri=f"s3://test-bucket/{key}",
        content_sha256=sha,
        byte_size=len(body),
        page_count=None,
        full_text=body.decode(),
        status="extracted",
    )
    created.append(doc_id)
    object_content_types = {key: "text/plain"}

    def fake_put(self, put_key, data, content_type):
        assert data == body
        object_content_types[put_key] = content_type
        return f"s3://{self.bucket}/{put_key}"

    monkeypatch.setattr(ObjectStore, "put", fake_put)
    schema = build_schema()
    upload = UploadFile(
        io.BytesIO(body),
        filename="already-stored.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def check():
        aconn = await _aconn()
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation($f: Upload!){ ingestDocument(file: $f, pipeline: false) "
                "{ kind status } }",
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["ingestDocument"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())
    assert object_content_types[key] == "text/plain"


def test_create_backup_allows_admin_principal(pg):
    conn, created = pg
    admin = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"admin-{new_ulid_str()}",),
    ).fetchone()["id"]
    admin_role_id = conn.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s)",
        (admin, admin_role_id),
    )
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(admin))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="admin", principal_id=str(admin), scopes=frozenset({"read", "write"})
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation { createBackup(note: "x") { kind status } }',
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["createBackup"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())
    conn.execute(
        "DELETE FROM jobs WHERE kind = 'backup' AND document_id IS NULL AND payload->>'note' = 'x'"
    )
    conn.execute("DELETE FROM principal_roles WHERE principal_id = %s", (admin,))
    conn.execute("DELETE FROM principals WHERE id = %s", (admin,))


def test_cancel_job_requires_admin_and_marks_job_canceled(pg):
    from doc_ray.adapters import queue_postgres as queue
    from doc_ray.domain.jobs import JobStatus

    conn, created = pg
    admin = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"cancel-admin-{new_ulid_str()}",),
    ).fetchone()["id"]
    non_admin = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (f"cancel-non-admin-{new_ulid_str()}",),
    ).fetchone()["id"]
    admin_role_id = conn.execute("SELECT id FROM roles WHERE name = 'admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s)",
        (admin, admin_role_id),
    )
    job_id = queue.enqueue(conn, "canonicalize", {"resolve": True}, document_id=None)
    schema = build_schema()

    async def check():
        non_admin_conn = await _aconn_as(str(non_admin))
        try:
            non_admin_ctx = RequestContext(
                conn=non_admin_conn,
                principal=Principal(
                    subject="non-admin",
                    principal_id=str(non_admin),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(non_admin_conn),
            )
            rejected = await schema.execute(
                "mutation($id: ID!) { cancelJob(jobId: $id) { status } }",
                variable_values={"id": job_id},
                context_value=non_admin_ctx,
            )
            assert rejected.errors is not None
            assert "admin" in str(rejected.errors[0]).lower()
        finally:
            await non_admin_conn.close()

        admin_conn = await _aconn_as(str(admin))
        try:
            admin_ctx = RequestContext(
                conn=admin_conn,
                principal=Principal(
                    subject="admin",
                    principal_id=str(admin),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(admin_conn),
            )
            canceled = await schema.execute(
                "mutation($id: ID!) { cancelJob(jobId: $id) { id status } }",
                variable_values={"id": job_id},
                context_value=admin_ctx,
            )
            assert canceled.errors is None, canceled.errors
            assert canceled.data["cancelJob"]["id"] == job_id
            assert canceled.data["cancelJob"]["status"] == "canceled"
        finally:
            await admin_conn.close()

    try:
        asyncio.run(check())
        assert queue.get(conn, job_id).status is JobStatus.CANCELED
    finally:
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
        conn.execute("DELETE FROM principal_roles WHERE principal_id = %s", (admin,))
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (admin, non_admin))


# ---------------------------------------------------------------------------
# BL-109 — write-mutation arguments must carry the same bounds read resolvers
# enforce: suggestSeeds.limit clamped through page_size(), decideSeeds's
# approve/reject arrays capped and rejected (not silently truncated) if too big.
# ---------------------------------------------------------------------------
