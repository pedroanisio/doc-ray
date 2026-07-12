"""API metrics endpoint tests (BL-118)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.metrics import (
    JOB_STATUS_METRICS_QUERY,
    metrics_response,
    register_metrics_route,
)


class _MetricsCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _MetricsConnection:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return _MetricsCursor(self.rows)


class _MetricsConnectionContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _MetricsPool:
    def __init__(self, rows):
        self.conn = _MetricsConnection(rows)

    def connection(self):
        return _MetricsConnectionContext(self.conn)


def test_metrics_response_exposes_prometheus_job_status_counts():
    pool = _MetricsPool(
        [
            {"status": "queued", "count": 2},
            {"status": "running", "count": 1},
            {"status": "succeeded", "count": 5},
        ]
    )

    async def check():
        return await metrics_response(pool)

    response = asyncio.run(check())

    assert response.status_code == 200
    assert response.media_type.startswith("text/plain")
    body = response.body.decode()
    assert "# TYPE docray_build_info gauge" in body
    assert 'docray_jobs_by_status{status="queued"} 2' in body
    assert 'docray_jobs_by_status{status="running"} 1' in body
    assert 'docray_jobs_by_status{status="succeeded"} 5' in body
    assert "docray_job_queue_depth 3" in body
    assert pool.conn.queries == [JOB_STATUS_METRICS_QUERY]


def test_register_metrics_route_adds_scrape_endpoint():
    app = FastAPI()

    register_metrics_route(app)

    assert "/metrics" in {getattr(route, "path", None) for route in app.routes}


def test_create_app_registers_metrics_endpoint():
    app = create_app(
        Config(
            database_url="postgresql://example",
            s3_endpoint="http://localhost:9000",
            s3_access_key="minioadmin",
            s3_secret_key="minioadmin",
            s3_bucket="documents",
            s3_region="us-east-1",
        )
    )

    assert "/metrics" in {getattr(route, "path", None) for route in app.routes}
