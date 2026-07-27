"""GraphQL multipart upload + ingestDocument mutation tests.

Split out of test_api_app.py (D-015). The mutation tests run against a
session-local ``TEMP TABLE jobs`` shadow so they never enqueue onto the real
queue another worker could claim.
"""

from __future__ import annotations

import asyncio
import io
import json

import _itdb
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from starlette.datastructures import Headers, UploadFile

from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal

WRITE_PRINCIPAL = Principal(
    subject="test-writer",
    principal_id="00000000-0000-0000-0000-000000000000",
    scopes=frozenset({"read", "write"}),
)


def _cfg() -> Config:
    """A Config bound to the test database with S3 settings from the environment."""
    base = Config.from_env()
    return Config(
        database_url=_itdb.DB_URL,
        s3_endpoint=base.s3_endpoint,
        s3_access_key=base.s3_access_key,
        s3_secret_key=base.s3_secret_key,
        s3_bucket=base.s3_bucket,
        s3_region=base.s3_region,
    )


def _upload_form(query: str) -> dict:
    """Build a GraphQL multipart-request form (jaydenseric multipart spec).

    Maps a single file to ``variables.f``; the caller supplies the operation
    ``query`` (``$f: Upload!``). Returned in httpx ``files=`` tuple form so the
    request carries a real ``multipart/form-data`` body.
    """
    return {
        "operations": (None, json.dumps({"query": query, "variables": {"f": None}})),
        "map": (None, json.dumps({"0": ["variables.f"]})),
        "0": ("regression.txt", b"regression upload bytes", "text/plain"),
    }


def test_graphql_router_enables_multipart_uploads():
    """Regression (D-001): the mounted GraphQL router must opt into file uploads.

    Strawberry disables multipart uploads by default (>=0.243), which silently
    breaks ``ingestDocument`` at the transport layer. ``create_app`` must set
    ``multipart_uploads_enabled`` on the router it mounts, so the upload surface
    stays wired. This guard runs offline (no DB/object store) and fails the
    instant the flag is dropped.
    """
    app = create_app(_cfg())
    assert app.state.graphql_router.multipart_uploads_enabled is True


def test_ingest_upload_transport_accepts_multipart():
    """Regression: an HTTP ``multipart/form-data`` upload must reach execution.

    With uploads disabled the transport rejects the request with HTTP 400
    ("Unsupported content type") *before* the resolver runs. We send a
    deliberately invalid query so execution stops at validation — HTTP 200 with
    a GraphQL error — proving the multipart transport is enabled without hitting
    the object store or enqueuing a job. Mirrors ``test_app_serves_health_and_graphql``
    (needs the live DB the pool opens on startup).
    """
    app = create_app(_cfg())
    with TestClient(app) as client:  # context manager runs the lifespan (pool open/close)
        resp = client.post(
            "/graphql", files=_upload_form("mutation($f: Upload!){ noSuchField(file: $f) }")
        )
    assert resp.status_code == 200, (
        f"multipart rejected at transport: {resp.status_code} {resp.text!r}"
    )
    body = resp.json()
    assert body.get("data") is None
    assert body["errors"], body
    assert "noSuchField" in body["errors"][0]["message"]


def test_ingest_document_upload_mutation(pg):
    """The upload mutation stores the blob (real MinIO) and enqueues on the shadow queue."""
    try:  # skip when MinIO is not running — the blob PUT is real
        ObjectStore(Config.from_env()).ensure_bucket()
    except Exception as exc:
        pytest.skip(f"object store unreachable: {exc}")

    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-app")
    schema = build_schema()
    body = b"Uploaded through GraphQL."
    upload = UploadFile(
        io.BytesIO(body),
        filename="upload-test.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def check():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            await aconn.execute("GRANT ALL PRIVILEGES ON jobs TO docray_batch")
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            r = await schema.execute(
                'mutation($f: Upload!){ ingestDocument(file: $f, title: "itup") { kind status } }',
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert r.errors is None, r.errors
            assert r.data["ingestDocument"]["kind"] == "ingest"
            assert r.data["ingestDocument"]["status"] == "queued"
            cur = await aconn.execute("SELECT payload FROM jobs")
            payload = (await cur.fetchone())["payload"]
            assert payload["original_filename"] == "upload-test.txt"
            assert payload["chain"] == [
                "annotate",
                "semantic_enrich",
                "canonicalize",
                "visual_extract",
            ]
        finally:
            await aconn.close()

    asyncio.run(check())


def test_ingest_document_upload_mutation_pipeline_false_skips_chain(pg):
    """``pipeline: false`` must ingest-only — no chain in the enqueued payload."""
    try:  # skip when MinIO is not running — the blob PUT is real
        ObjectStore(Config.from_env()).ensure_bucket()
    except Exception as exc:
        pytest.skip(f"object store unreachable: {exc}")

    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-app")
    schema = build_schema()
    body = b"Ingest-only through GraphQL."
    upload = UploadFile(
        io.BytesIO(body),
        filename="ingest-only.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    async def check():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            await aconn.execute("GRANT ALL PRIVILEGES ON jobs TO docray_batch")
            ctx = RequestContext(
                conn=aconn,
                principal=WRITE_PRINCIPAL,
                loaders=Loaders(aconn),
                request_id="req-api-ingest",
            )
            r = await schema.execute(
                "mutation($f: Upload!){ ingestDocument(file: $f, pipeline: false) { kind } }",
                variable_values={"f": upload},
                context_value=ctx,
            )
            assert r.errors is None, r.errors
            cur = await aconn.execute("SELECT payload FROM jobs")
            payload = (await cur.fetchone())["payload"]
            assert "chain" not in payload
            assert payload["_meta"]["request_id"] == "req-api-ingest"
        finally:
            await aconn.close()

    asyncio.run(check())


# --- ingest by reference (no upload) ----------------------------------------


def _ref_mutation(ref: str, *, pipeline: bool = True) -> tuple[str, dict]:
    """The ref-shaped ingestDocument call and its variables."""
    return (
        "mutation($r: String, $p: Boolean!)"
        "{ ingestDocument(ref: $r, pipeline: $p) { kind status } }",
        {"r": ref, "p": pipeline},
    )


def _run_mutation(query: str, variables: dict):
    """Execute *query* against a shadow ``jobs`` table; return (result, payloads)."""
    schema = build_schema()
    captured: dict = {}

    async def check():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
            await aconn.execute("GRANT ALL PRIVILEGES ON jobs TO docray_batch")
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            result = await schema.execute(query, variable_values=variables, context_value=ctx)
            cur = await aconn.execute("SELECT kind, payload FROM jobs")
            captured["rows"] = await cur.fetchall()
            captured["result"] = result
        finally:
            await aconn.close()

    asyncio.run(check())
    return captured["result"], captured["rows"]


def test_ingest_document_accepts_a_remote_reference(pg):
    """A reference enqueues an ingest job carrying the ref — no blob, no upload.

    The arXiv source adapter shipped without any way to reach it but the CLI;
    this is the API half. The worker resolves and fetches the ref, so the
    mutation must not block the request on a remote download.
    """
    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")
    query, variables = _ref_mutation("https://arxiv.org/abs/2401.00001")

    result, rows = _run_mutation(query, variables)

    assert result.errors is None, result.errors
    assert result.data["ingestDocument"]["kind"] == "ingest"
    assert result.data["ingestDocument"]["status"] == "queued"
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["ref"] == "https://arxiv.org/abs/2401.00001"
    assert "storage_key" not in payload
    assert payload["chain"] == ["annotate", "semantic_enrich", "canonicalize", "visual_extract"]
    assert payload["owner_principal_id"] == WRITE_PRINCIPAL.principal_id


def test_ingest_document_reference_honours_pipeline_false(pg):
    """``pipeline: false`` opts a referenced ingest out of the chain, like an upload."""
    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")
    query, variables = _ref_mutation("arxiv:2401.00002", pipeline=False)

    result, rows = _run_mutation(query, variables)

    assert result.errors is None, result.errors
    assert "chain" not in rows[0]["payload"]


def test_ingest_document_rejects_an_unresolvable_reference(pg):
    """A ref no source adapter claims fails at the mutation, not silently in a worker.

    Enqueuing a job that is guaranteed to fail would make the caller poll for a
    failure the API already knew about.
    """
    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")
    query, variables = _ref_mutation("https://example.org/not-a-known-source.pdf")

    result, rows = _run_mutation(query, variables)

    assert result.errors, "an unresolvable reference must be rejected"
    assert "no source adapter" in str(result.errors[0]).lower()
    assert rows == []


def test_ingest_document_requires_exactly_one_of_file_or_ref(pg):
    """Neither argument is a caller error; the message must name both options."""
    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")

    result, rows = _run_mutation(
        "mutation{ ingestDocument(pipeline: true) { kind status } }",
        {},
    )

    assert result.errors, "an ingest with neither a file nor a ref must be rejected"
    message = str(result.errors[0]).lower()
    assert "file" in message and "ref" in message
    assert rows == []


def test_ingest_document_rejects_a_file_and_a_ref_together(pg):
    """Two intake routes in one call is ambiguous — reject rather than pick one."""
    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")
    upload = UploadFile(
        io.BytesIO(b"Both at once."),
        filename="both.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    result, rows = _run_mutation(
        "mutation($f: Upload, $r: String)"
        "{ ingestDocument(file: $f, ref: $r, pipeline: true) { kind status } }",
        {"f": upload, "r": "arxiv:2401.00003"},
    )

    assert result.errors, "supplying both a file and a ref must be rejected"
    assert rows == []


def test_ingest_document_trims_surrounding_whitespace_from_a_reference(pg):
    """A pasted reference often carries whitespace; it must not reach the worker."""
    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")
    query, variables = _ref_mutation("  https://arxiv.org/abs/2401.00009\n")

    result, rows = _run_mutation(query, variables)

    assert result.errors is None, result.errors
    assert rows[0]["payload"]["ref"] == "https://arxiv.org/abs/2401.00009"


def test_ingest_document_treats_a_blank_reference_as_absent(pg):
    """A blank ``ref`` alongside a file is an empty form field, not a second route.

    Browsers post empty text inputs, so a whitespace-only ref must not be
    mistaken for "both routes supplied" and reject a legitimate upload.
    """
    try:
        ObjectStore(Config.from_env()).ensure_bucket()
    except Exception as exc:
        pytest.skip(f"object store unreachable: {exc}")

    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")
    upload = UploadFile(
        io.BytesIO(b"Blank ref beside a real file."),
        filename="blank-ref.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    result, rows = _run_mutation(
        "mutation($f: Upload, $r: String)"
        "{ ingestDocument(file: $f, ref: $r, pipeline: true) { kind status } }",
        {"f": upload, "r": "   "},
    )

    assert result.errors is None, result.errors
    assert len(rows) == 1
    assert "storage_key" in rows[0]["payload"]
    assert "ref" not in rows[0]["payload"]


def test_ingest_document_upload_route_is_unchanged_by_the_reference_route(pg):
    """Regression: making ``file`` nullable must not weaken the upload contract.

    The extension allowlist (BL-106) still runs before the blob is stored.
    """
    conn, _created = pg
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-ref")
    upload = UploadFile(
        io.BytesIO(b"payload"),
        filename="danger.exe",
        headers=Headers({"content-type": "application/octet-stream"}),
    )

    result, rows = _run_mutation(
        "mutation($f: Upload){ ingestDocument(file: $f, pipeline: true) { kind status } }",
        {"f": upload},
    )

    assert result.errors, "an unsupported extension must still be rejected"
    assert "unsupported document type" in str(result.errors[0])
    assert rows == []
