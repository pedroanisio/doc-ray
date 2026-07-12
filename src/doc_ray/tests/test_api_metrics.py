"""API metrics endpoint tests (BL-118)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from doc_ray.config import Config
from doc_ray.entrypoints.api.app import create_app
from doc_ray.entrypoints.api.metrics import (
    INGEST_THROUGHPUT_QUERY,
    JOB_KIND_METRICS_QUERY,
    JOB_STATUS_METRICS_QUERY,
    HTTPRequestMetrics,
    metrics_response,
    register_metrics_route,
)


class _MetricsCursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _MetricsConnection:
    def __init__(self, rows_by_query):
        self.rows_by_query = rows_by_query
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return _MetricsCursor(self.rows_by_query.get(query, []))


class _MetricsConnectionContext:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _MetricsPool:
    def __init__(self, rows_by_query):
        self.conn = _MetricsConnection(rows_by_query)

    def connection(self):
        return _MetricsConnectionContext(self.conn)


def test_metrics_response_exposes_prometheus_job_status_counts():
    pool = _MetricsPool(
        {
            JOB_STATUS_METRICS_QUERY: [
                {"status": "queued", "count": 2},
                {"status": "running", "count": 1},
                {"status": "succeeded", "count": 5},
            ],
            JOB_KIND_METRICS_QUERY: [],
            INGEST_THROUGHPUT_QUERY: [{"count": 0}],
        }
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
    assert pool.conn.queries == [
        JOB_STATUS_METRICS_QUERY,
        JOB_KIND_METRICS_QUERY,
        INGEST_THROUGHPUT_QUERY,
    ]


def test_metrics_response_exposes_job_latency_and_ingest_throughput():
    pool = _MetricsPool(
        {
            JOB_STATUS_METRICS_QUERY: [],
            JOB_KIND_METRICS_QUERY: [
                {
                    "kind": "ingest",
                    "succeeded_count": 4,
                    "failed_count": 1,
                    "completed_count": 4,
                    "duration_sum_seconds": 14.5,
                    "duration_max_seconds": 6.0,
                }
            ],
            INGEST_THROUGHPUT_QUERY: [{"count": 3}],
        }
    )

    async def check():
        return await metrics_response(pool)

    response = asyncio.run(check())
    body = response.body.decode()

    assert 'docray_jobs_completed_total{kind="ingest",status="succeeded"} 4' in body
    assert 'docray_jobs_completed_total{kind="ingest",status="failed"} 1' in body
    assert 'docray_job_duration_seconds_count{kind="ingest"} 4' in body
    assert 'docray_job_duration_seconds_sum{kind="ingest"} 14.5' in body
    assert 'docray_job_duration_seconds_max{kind="ingest"} 6.0' in body
    assert "docray_ingest_throughput_jobs_per_hour 3" in body


def test_http_request_metrics_exposes_graphql_latency():
    request_metrics = HTTPRequestMetrics()
    request_metrics.observe(path="/graphql", method="POST", status_code=200, duration_seconds=0.25)
    request_metrics.observe(path="/graphql", method="POST", status_code=200, duration_seconds=0.75)
    request_metrics.observe(path="/health", method="GET", status_code=200, duration_seconds=1.0)
    pool = _MetricsPool(
        {
            JOB_STATUS_METRICS_QUERY: [],
            JOB_KIND_METRICS_QUERY: [],
            INGEST_THROUGHPUT_QUERY: [{"count": 0}],
        }
    )

    async def check():
        return await metrics_response(pool, request_metrics=request_metrics)

    body = asyncio.run(check()).body.decode()

    assert 'docray_graphql_request_duration_seconds_count{method="POST",status="200"} 2' in body
    assert 'docray_graphql_request_duration_seconds_sum{method="POST",status="200"} 1.0' in body
    assert 'docray_graphql_request_duration_seconds_max{method="POST",status="200"} 0.75' in body


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
    assert isinstance(app.state.http_request_metrics, HTTPRequestMetrics)
