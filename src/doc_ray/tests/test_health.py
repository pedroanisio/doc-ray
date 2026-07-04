"""Readiness probes and the /readyz endpoint that keep dependency failures visible.

Regression cover for the silent-failure class where the API is *up* but cannot reach its
object store (an ``S3_ENDPOINT`` pointing nowhere), so ``ingestDocument`` blew up only at
upload time. Readiness must surface that as HTTP 503 with a per-dependency diagnostic.
"""

from __future__ import annotations

import asyncio

import _itdb

from doc_ray.config import Config
from doc_ray.entrypoints.api import health
from doc_ray.entrypoints.api.app import create_app


class _OkStore:
    """Object-store double whose reachability probe succeeds."""

    def ping(self) -> None:
        return None


class _DownStore:
    """Object-store double whose reachability probe fails like an unreachable endpoint."""

    def ping(self) -> None:
        raise OSError("Could not connect to the endpoint URL: http://localhost:9000")


class _FakeConn:
    def __init__(self, fail: bool) -> None:
        self._fail = fail

    async def execute(self, _sql: str) -> None:
        if self._fail:
            raise RuntimeError("connection refused")


class _FakePool:
    """Minimal async pool: ``connection()`` is an async context manager."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    def connection(self):  # noqa: ANN201 - test double
        pool = self

        class _Ctx:
            async def __aenter__(self) -> _FakeConn:
                return _FakeConn(pool._fail)

            async def __aexit__(self, *_exc: object) -> bool:
                return False

        return _Ctx()


# ---- pure probe logic -------------------------------------------------------


def test_probe_object_store_ok():
    assert health.probe_object_store(_OkStore()) == "ok"


def test_probe_object_store_unreachable_reports_reason():
    status = health.probe_object_store(_DownStore())
    assert status.startswith("unreachable:")
    assert "endpoint" in status.lower()


def test_probe_database_ok_and_error():
    assert asyncio.run(health.probe_database(_FakePool(fail=False))) == "ok"
    assert asyncio.run(health.probe_database(_FakePool(fail=True))).startswith("error:")


def test_readiness_ready_only_when_all_ok():
    ready = health.readiness({"database": "ok", "object_store": "ok"})
    assert ready == {
        "status": "ready",
        "ready": True,
        "checks": {"database": "ok", "object_store": "ok"},
    }

    degraded = health.readiness({"database": "ok", "object_store": "unreachable: boom"})
    assert degraded["ready"] is False
    assert degraded["status"] == "not_ready"


# ---- endpoint wiring (real DB via the pool; store swapped on app.state) ------


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


def test_health_liveness_is_unconditional():
    from fastapi.testclient import TestClient

    app = create_app(_cfg())
    app.state.object_store = _DownStore()  # liveness must not depend on it
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_readyz_200_when_dependencies_reachable():
    from fastapi.testclient import TestClient

    app = create_app(_cfg())
    app.state.object_store = _OkStore()
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"] == {"database": "ok", "object_store": "ok"}
    assert body["version"]


def test_readyz_503_when_object_store_unreachable():
    """The exact regression: API up, store unreachable -> 503, not a silent upload failure."""
    from fastapi.testclient import TestClient

    app = create_app(_cfg())
    app.state.object_store = _DownStore()
    with TestClient(app) as client:
        resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["ready"] is False
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["object_store"].startswith("unreachable:")
