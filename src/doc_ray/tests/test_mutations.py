"""GraphQL mutation tests: enqueue jobs via schema.execute (skip without a DB).

Seeds a document (sync), ensures the jobs table, then runs mutations against an async
connection and checks a queued job is created and readable via the job query. Jobs are
cleaned up by cascade when the seeded document is deleted in teardown.
"""

from __future__ import annotations

import asyncio

import pytest
from _mutations_common import WRITE_PRINCIPAL, _aconn, _aconn_as, _seed

from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal


def test_reannotate_and_delete_enqueue(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            reann = await schema.execute(
                'mutation ($id: ID!) { reannotate(documentId: $id, lang: "en") '
                "{ id kind status documentId } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert reann.errors is None, reann.errors
            job = reann.data["reannotate"]
            assert job["kind"] == "reannotate"
            assert job["status"] == "queued"
            assert job["documentId"] == doc_id

            # The job query reads it back.
            polled = await schema.execute(
                "query ($id: ID!) { job(id: $id) { kind status } }",
                variable_values={"id": job["id"]},
                context_value=ctx,
            )
            assert polled.errors is None, polled.errors
            assert polled.data["job"]["kind"] == "reannotate"

            deletion = await schema.execute(
                "mutation ($id: ID!) { deleteDocument(documentId: $id) { kind status } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert deletion.errors is None, deletion.errors
            assert deletion.data["deleteDocument"]["kind"] == "delete"
            assert deletion.data["deleteDocument"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())


def test_document_list_exposes_lifecycle_fields(pg):
    conn, _created = pg
    doc_id = _seed(pg)
    conn.execute(
        "UPDATE documents SET lifecycle_state = 'archived', archived_at = now() WHERE id = %s",
        (doc_id,),
    )
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "{ documents(limit: 200) { id lifecycleState archivedAt deletedAt } }",
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            row = next(r for r in result.data["documents"] if r["id"] == doc_id)
            assert row["lifecycleState"] == "archived"
            assert row["archivedAt"] is not None
            assert row["deletedAt"] is None
        finally:
            await aconn.close()

    asyncio.run(check())


def test_bulk_document_actions_enqueue_per_document_jobs(pg):
    first = _seed(pg)
    second = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            tagged = await schema.execute(
                "mutation($ids: [ID!]!, $tags: [String!]!) { "
                "bulkTagDocuments(documentIds: $ids, tags: $tags) { kind status documentId } }",
                variable_values={"ids": [first, second], "tags": ["reviewed", "ops"]},
                context_value=ctx,
            )
            assert tagged.errors is None, tagged.errors
            assert [j["kind"] for j in tagged.data["bulkTagDocuments"]] == [
                "add_document_tags",
                "add_document_tags",
            ]
            assert [j["documentId"] for j in tagged.data["bulkTagDocuments"]] == [first, second]

            archived = await schema.execute(
                "mutation($ids: [ID!]!) { "
                "bulkArchiveDocuments(documentIds: $ids) { kind status documentId } }",
                variable_values={"ids": [first, second]},
                context_value=ctx,
            )
            assert archived.errors is None, archived.errors
            assert [j["kind"] for j in archived.data["bulkArchiveDocuments"]] == [
                "archive_document",
                "archive_document",
            ]
            assert [j["documentId"] for j in archived.data["bulkArchiveDocuments"]] == [
                first,
                second,
            ]
        finally:
            await aconn.close()

    asyncio.run(check())


def test_metadata_suggestion_mutations_enqueue_and_decide(pg):
    doc_id = _seed(pg)
    conn, _created = pg
    sid = new_ulid_str()
    conn.execute(
        "INSERT INTO document_metadata_suggestion "
        "(id, document_id, field, suggested_value, confidence, reason, source) "
        "VALUES (%s, %s, 'tags', 'graph', 0.72, 'frequent term', 'verified-deterministic')",
        (sid, doc_id),
    )
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            enqueued = await schema.execute(
                "mutation($id: ID!) { suggestDocumentMetadata(documentId: $id) "
                "{ kind status documentId } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert enqueued.errors is None, enqueued.errors
            assert enqueued.data["suggestDocumentMetadata"]["kind"] == "suggest_document_metadata"

            decided = await schema.execute(
                "mutation($ids: [ID!]!) { decideDocumentMetadataSuggestions("
                "approveIds: $ids) }",
                variable_values={"ids": [sid]},
                context_value=ctx,
            )
            assert decided.errors is None, decided.errors
            assert decided.data["decideDocumentMetadataSuggestions"] == 1

            listed = await schema.execute(
                "query($id: ID!) { documentMetadataSuggestions(documentId: $id) "
                "{ id field suggestedValue confidence confidenceBand status } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert listed.errors is None, listed.errors
            assert listed.data["documentMetadataSuggestions"][0]["confidenceBand"] == "low"
        finally:
            await aconn.close()

    asyncio.run(check())


# ---------------------------------------------------------------------------
# BL-064 — a global "write" scope must not let a principal mutate a document
# outside their own document_acl grants; jobs/backups must enforce this at
# the RLS layer too, not just in the resolver.
# ---------------------------------------------------------------------------


def _make_principal(conn, *, display_name: str) -> str:
    return conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (display_name,),
    ).fetchone()["id"]


def test_reannotate_rejects_write_scope_without_document_grant(pg):
    """Global write scope alone must not authorize mutating an arbitrary document."""
    conn, created = pg
    doc_id = _seed(pg)
    outsider = _make_principal(conn, display_name=f"outsider-{new_ulid_str()}")
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(outsider))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="outsider",
                    principal_id=str(outsider),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation ($id: ID!) { reannotate(documentId: $id, lang: "en") { id } }',
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert result.errors is not None
            assert "write grant" in str(result.errors[0]).lower()
        finally:
            await aconn.close()

    asyncio.run(check())
    # No job was actually enqueued for this document by the rejected principal.
    assert (
        conn.execute("SELECT count(*) AS n FROM jobs WHERE document_id = %s", (doc_id,)).fetchone()[
            "n"
        ]
        == 0
    )
    conn.execute("DELETE FROM principals WHERE id = %s", (outsider,))


def test_reannotate_allows_write_scope_with_document_grant(pg):
    """A principal holding an explicit document_acl grant may mutate that document."""
    conn, created = pg
    doc_id = _seed(pg)
    grantee = _make_principal(conn, display_name=f"grantee-{new_ulid_str()}")
    conn.execute(
        "INSERT INTO document_acl (document_id, principal_id, permission) VALUES (%s, %s, 'annotate')",
        (doc_id, grantee),
    )
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(str(grantee))
        try:
            ctx = RequestContext(
                conn=aconn,
                principal=Principal(
                    subject="grantee",
                    principal_id=str(grantee),
                    scopes=frozenset({"read", "write"}),
                ),
                loaders=Loaders(aconn),
            )
            result = await schema.execute(
                'mutation ($id: ID!) { reannotate(documentId: $id, lang: "en") { kind status } }',
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["reannotate"]["status"] == "queued"
        finally:
            await aconn.close()

    asyncio.run(check())
    conn.execute("DELETE FROM principals WHERE id = %s", (grantee,))


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


# ---------------------------------------------------------------------------
# BL-109 — write-mutation arguments must carry the same bounds read resolvers
# enforce: suggestSeeds.limit clamped through page_size(), decideSeeds's
# approve/reject arrays capped and rejected (not silently truncated) if too big.
# ---------------------------------------------------------------------------


def test_suggest_seeds_clamps_limit_through_page_size(pg):
    from doc_ray.entrypoints.api.context import MAX_PAGE_SIZE

    conn, created = pg
    doc_id = _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation($id: ID!) { suggestSeeds(documentId: $id, limit: 999999) { id } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
        finally:
            await aconn.close()

    asyncio.run(check())
    row = conn.execute(
        "SELECT payload->>'limit' AS lim FROM jobs WHERE kind = 'seed_suggest' "
        "AND document_id = %s ORDER BY created_at DESC LIMIT 1",
        (doc_id,),
    ).fetchone()
    assert row is not None
    assert int(row["lim"]) == MAX_PAGE_SIZE


def test_reannotate_rejects_malformed_document_id_without_leaking_postgres_error(pg):
    """BL-110: _require_document_write's ::uuid cast must not leak a raw Postgres error."""
    _seed(pg)
    schema = build_schema()

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                'mutation { reannotate(documentId: "not-a-uuid", lang: "en") { id } }',
                context_value=ctx,
            )
            assert result.errors is not None
            msg = str(result.errors[0]).lower()
            assert "no write grant" in msg
            assert "invalid input syntax" not in msg
        finally:
            await aconn.close()

    asyncio.run(check())


def test_decide_seeds_rejects_oversized_batch(pg):
    from doc_ray.entrypoints.api.context import MAX_BATCH_IDS

    _seed(pg)
    schema = build_schema()
    too_many = [new_ulid_str() for _ in range(MAX_BATCH_IDS + 1)]

    async def check():
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(
                "mutation($ids: [ID!]) { decideSeeds(approveIds: $ids) }",
                variable_values={"ids": too_many},
                context_value=ctx,
            )
            assert result.errors is not None
            assert str(MAX_BATCH_IDS) in str(result.errors[0])
        finally:
            await aconn.close()

    asyncio.run(check())
