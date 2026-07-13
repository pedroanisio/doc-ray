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
            assert payload["chain"] == ["annotate", "semantic_enrich", "canonicalize"]
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
