"""ASGI app wiring, principal binding (security), and enqueue mutations.

The mutation test runs against a session-local ``TEMP TABLE jobs`` shadow so it
never enqueues onto the real queue another worker could claim.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import _itdb
import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

import doc_ray.entrypoints.api.app as api_app
from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import (
    ANONYMOUS,
    HeaderTokenAuthenticator,
    NoopAuthenticator,
    Principal,
    apply_principal,
    reset_principal,
)

WRITE_PRINCIPAL = Principal(
    subject="test-writer",
    principal_id="00000000-0000-0000-0000-000000000000",
    scopes=frozenset({"read", "write"}),
)


def _cfg() -> Config:
    base = Config.from_env()
    return Config(
        database_url=_itdb.DB_URL,
        s3_endpoint=base.s3_endpoint,
        s3_access_key=base.s3_access_key,
        s3_secret_key=base.s3_secret_key,
        s3_bucket=base.s3_bucket,
        s3_region=base.s3_region,
    )


def test_app_serves_health_and_graphql(monkeypatch):
    logging_calls = []  # BL-115: create_app must configure logging once at startup.
    monkeypatch.setattr(api_app, "configure_logging", lambda: logging_calls.append(1))
    app = create_app(_cfg())
    assert logging_calls == [1]
    with TestClient(app) as client:  # context manager runs the lifespan (pool open/close)
        health = client.get("/health").json()
        assert health["status"] == "ok"
        # optional version report (semantic-version string when present)
        assert isinstance(health.get("version", ""), str)
        r = client.post("/graphql", json={"query": "{ documents(limit: 1) { id } }"})
        assert r.status_code == 200
        body = r.json()
        assert body.get("errors") is None
        assert isinstance(body["data"]["documents"], list)


@pytest.mark.parametrize(("debug", "expected_status"), [(False, 404), (True, 200)])
def test_graphiql_gated_by_debug_flag(pg, debug, expected_status):
    # BL-108: an anonymous caller (the default, debug=False) must not get an
    # interactive schema explorer for free reconnaissance.
    app = create_app(replace(_cfg(), api_debug=debug))
    with TestClient(app) as client:
        r = client.get("/graphql", headers={"accept": "text/html"})
        assert r.status_code == expected_status


def test_noop_authenticator_returns_anonymous():
    async def check():
        principal = await NoopAuthenticator().authenticate(None)  # type: ignore[arg-type]
        assert principal is ANONYMOUS

    asyncio.run(check())


def test_header_token_authenticator_maps_bearer_token_to_principal():
    token = "secret-token"
    principal_id = str(uuid4())
    authenticator = HeaderTokenAuthenticator(tokens={token: principal_id})

    class Request:
        headers = {"authorization": f"Bearer {token}"}

    async def check():
        principal = await authenticator.authenticate(Request())  # type: ignore[arg-type]
        assert principal.principal_id == principal_id
        assert principal.subject == "token:secret-token"
        assert principal.scopes == frozenset({"read", "write"})

    asyncio.run(check())


def test_header_token_authenticator_can_fail_closed():
    authenticator = HeaderTokenAuthenticator(tokens={"known": str(uuid4())}, allow_anonymous=False)

    class Request:
        headers: dict[str, str] = {}

    async def check():
        with pytest.raises(HTTPException) as exc:
            await authenticator.authenticate(Request())  # type: ignore[arg-type]
        assert exc.value.status_code == 401

    asyncio.run(check())


def test_config_reads_api_pool_and_auth_settings(monkeypatch):
    principal_id = str(uuid4())
    monkeypatch.setenv("DOCRAY_API_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("DOCRAY_API_POOL_MAX_SIZE", "9")
    monkeypatch.setenv("DOCRAY_API_POOL_TIMEOUT", "3.5")
    monkeypatch.setenv("DOCRAY_API_STATEMENT_TIMEOUT_MS", "5000")
    monkeypatch.setenv("DOCRAY_API_TOKENS", f"tok={principal_id}:read")
    monkeypatch.setenv("DOCRAY_API_ALLOW_ANONYMOUS", "false")

    cfg = Config.from_env()

    assert cfg.api_pool_min_size == 2
    assert cfg.api_pool_max_size == 9
    assert cfg.api_pool_timeout == 3.5
    assert cfg.api_statement_timeout_ms == 5000
    assert cfg.api_tokens == {"tok": (principal_id, frozenset({"read"}))}
    assert cfg.api_allow_anonymous is False


def test_config_defaults_statement_timeout_to_fifteen_seconds(monkeypatch):
    monkeypatch.delenv("DOCRAY_API_STATEMENT_TIMEOUT_MS", raising=False)

    assert Config.from_env().api_statement_timeout_ms == 15_000


class _FakePool:
    """Records constructor kwargs; never opened (lifespan is not entered here)."""

    def __init__(self, dsn, *, open, kwargs, min_size, max_size, timeout):
        self.dsn = dsn
        self.open_arg = open
        self.kwargs = kwargs
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout

    async def open(self):  # pragma: no cover - lifespan is not entered
        raise AssertionError("not used")

    async def close(self):  # pragma: no cover - lifespan is not entered
        raise AssertionError("not used")


def test_create_app_passes_explicit_pool_bounds(monkeypatch):
    monkeypatch.setattr(api_app, "AsyncConnectionPool", _FakePool)

    cfg = replace(_cfg(), api_pool_min_size=2, api_pool_max_size=8, api_pool_timeout=4.0)
    app = create_app(cfg)

    assert app.state.pool.min_size == 2
    assert app.state.pool.max_size == 8
    assert app.state.pool.timeout == 4.0


def test_create_app_configures_statement_timeout(monkeypatch):
    monkeypatch.setattr(api_app, "AsyncConnectionPool", _FakePool)

    cfg = replace(_cfg(), api_statement_timeout_ms=7500)
    app = create_app(cfg)

    assert app.state.pool.kwargs["options"] == "-c statement_timeout=7500"


def test_apply_and_reset_principal_switch_roles(pg):
    conn, _ = pg
    row = conn.execute("SELECT id FROM principals WHERE display_name='bootstrap-admin'").fetchone()
    if row is None:
        pytest.skip("RBAC seed principal absent")
    principal = Principal(subject="t", principal_id=str(row["id"]), scopes=frozenset({"read"}))

    async def check():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            await apply_principal(aconn, principal)
            cur = await aconn.execute(
                "SELECT current_user AS u, current_setting('app.principal_id') AS p"
            )
            got = await cur.fetchone()
            assert got["u"] == "docray_api" and got["p"] == principal.principal_id
            await reset_principal(aconn, principal)
            cur = await aconn.execute("SELECT current_user AS u")
            got = await cur.fetchone()
            assert got["u"] != "docray_client"

            # Anonymous HTTP requests still drop to the RLS-limited API role;
            # they no longer stay on the trusted superuser connection.
            await apply_principal(aconn, ANONYMOUS)
            cur = await aconn.execute("SELECT current_user AS u")
            got = await cur.fetchone()
            assert got["u"] == "docray_api"
            await reset_principal(aconn, ANONYMOUS)
        finally:
            await aconn.close()

    asyncio.run(check())


async def _isolated_write_conn() -> psycopg.AsyncConnection:
    """A shadow-``jobs``-queue connection scoped as WRITE_PRINCIPAL (BL-064).

    ``LIKE ... INCLUDING ALL`` skips RLS, so grant before SET ROLE.
    """
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    await aconn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
    await aconn.execute("GRANT SELECT, INSERT ON jobs TO docray_client; SET ROLE docray_api")
    await aconn.execute(
        "SELECT set_config('app.principal_id', %s, false)", (WRITE_PRINCIPAL.principal_id,)
    )
    return aconn


def test_mutations_enqueue_jobs_on_isolated_queue(pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "A document to reannotate.")
    created.append(doc_id)
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-app")
    schema = build_schema()

    async def check():
        aconn = await _isolated_write_conn()
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))

            r = await schema.execute(
                'mutation($id:ID!){ reannotate(documentId:$id, lang:"en") { id kind status } }',
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert r.errors is None, r.errors
            job = r.data["reannotate"]
            assert job["kind"] == "reannotate" and job["status"] == "queued"

            r2 = await schema.execute(
                "mutation($id:ID!){ deleteDocument(documentId:$id) { kind status documentId } }",
                variable_values={"id": doc_id},
                context_value=ctx,
            )
            assert r2.errors is None, r2.errors
            assert r2.data["deleteDocument"]["kind"] == "delete"
            assert r2.data["deleteDocument"]["documentId"] == doc_id

            cur = await aconn.execute("SELECT count(*) AS n FROM jobs")
            n = (await cur.fetchone())["n"]
            assert n == 2  # both landed on the shadow queue only
        finally:
            await aconn.close()

    asyncio.run(check())


def test_anonymous_mutation_requires_write_scope(pg):
    schema = build_schema()

    async def check():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            ctx = RequestContext(conn=aconn, principal=ANONYMOUS, loaders=Loaders(aconn))
            result = await schema.execute(
                'mutation{ createBackup(note:"blocked"){ id } }',
                context_value=ctx,
            )
            assert result.errors is not None
            assert "write scope" in result.errors[0].message
        finally:
            await aconn.close()

    asyncio.run(check())


def test_seeding_and_backup_mutations_enqueue_on_isolated_queue(pg):
    # Covers the remaining mutation resolvers a shadow-queue round trip hadn't
    # exercised yet: suggestSeeds, both branches of decideSeeds individually,
    # createBackup, and restoreBackup.
    conn, created = pg
    from doc_ray.domain.ulid import new_ulid_str

    doc_id = _itdb.make_doc(conn, "A document to seed.")
    created.append(doc_id)
    _itdb.ensure_admin_principal(conn, WRITE_PRINCIPAL.principal_id, "test-writer-app")
    sid_a = new_ulid_str()
    sid_r = new_ulid_str()
    conn.execute(
        "INSERT INTO seed_suggestion (id, document_id, term, occurrences, qid, status) "
        "VALUES (%s,%s,'approve-me',1,'Q1','pending'), (%s,%s,'reject-me',1,'Q2','pending')",
        (sid_a, doc_id, sid_r, doc_id),
    )
    schema = build_schema()

    async def check():
        aconn = await _isolated_write_conn()
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))

            r = await schema.execute(
                "mutation($d:ID!){ suggestSeeds(documentId:$d){ kind status } }",
                variable_values={"d": doc_id},
                context_value=ctx,
            )
            assert r.errors is None, r.errors
            assert r.data["suggestSeeds"]["kind"] == "seed_suggest"

            # decideSeeds: approveIds-only and rejectIds-only each exercise the
            # OTHER argument's False branch.
            r2 = await schema.execute(
                "mutation($a:[ID!]){ decideSeeds(approveIds:$a) }",
                variable_values={"a": [sid_a]},
                context_value=ctx,
            )
            assert r2.errors is None, r2.errors
            assert r2.data["decideSeeds"] == 1

            r3 = await schema.execute(
                "mutation($r:[ID!]){ decideSeeds(rejectIds:$r) }",
                variable_values={"r": [sid_r]},
                context_value=ctx,
            )
            assert r3.errors is None, r3.errors
            assert r3.data["decideSeeds"] == 1

            r4 = await schema.execute(
                'mutation{ createBackup(note:"nightly"){ kind status } }',
                context_value=ctx,
            )
            assert r4.errors is None, r4.errors
            assert r4.data["createBackup"]["kind"] == "backup"

            r5 = await schema.execute(
                'mutation{ restoreBackup(backupId:"b1"){ kind status } }',
                context_value=ctx,
            )
            assert r5.errors is None, r5.errors
            assert r5.data["restoreBackup"]["kind"] == "restore"

            cur = await aconn.execute("SELECT count(*) AS n FROM jobs")
            n = (await cur.fetchone())["n"]
            assert n == 3  # suggest_seeds + backup + restore landed on the shadow queue
        finally:
            await aconn.close()

    asyncio.run(check())

    rows = conn.execute(
        "SELECT id, status FROM seed_suggestion WHERE id = ANY(%s)", ([sid_a, sid_r],)
    ).fetchall()
    by_id = {str(r["id"]): r["status"] for r in rows}
    assert by_id[sid_a] == "approved" and by_id[sid_r] == "rejected"


def test_document_satellites_resolve_in_listing(pg):
    """tags/metadata/tokenCount fields batch-resolve over the listing (loaders)."""
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Tagged and annotated.")
    created.append(doc_id)
    conn.execute("INSERT INTO tags (tag) VALUES ('ittag') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO document_tags (document_id, tag_id) "
        "SELECT %s, id FROM tags WHERE tag='ittag' ON CONFLICT DO NOTHING",
        (doc_id,),
    )
    conn.execute(
        "INSERT INTO document_metadata (document_id, key, value) VALUES (%s, 'itkey', 'itval') "
        "ON CONFLICT DO NOTHING",
        (doc_id,),
    )
    schema = build_schema()

    async def check():
        aconn = await psycopg.AsyncConnection.connect(
            _itdb.DB_URL, row_factory=dict_row, autocommit=True
        )
        try:
            ctx = RequestContext(conn=aconn, principal=WRITE_PRINCIPAL, loaders=Loaders(aconn))
            r = await schema.execute(
                "{ documents(limit: 200) { id tags tokenCount metadata { key value } } }",
                context_value=ctx,
            )
            assert r.errors is None, r.errors
            mine = next(d for d in r.data["documents"] if d["id"] == doc_id)
            assert mine["tags"] == ["ittag"]
            assert {"key": "itkey", "value": "itval"} in mine["metadata"]
            assert mine["tokenCount"] == 0  # no annotation run yet
        finally:
            await aconn.close()

    asyncio.run(check())
