"""Prometheus-compatible metrics for the API."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, Response
from psycopg_pool import AsyncConnectionPool

from doc_ray import __version__

JOB_STATUS_METRICS_QUERY = (
    "SELECT status, count(*) AS count FROM jobs GROUP BY status ORDER BY status"
)


def _prometheus_label(value: object) -> str:
    """Return a Prometheus text-format label value."""
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def format_metrics(rows: list[dict[str, Any]]) -> str:
    """Render API metrics using the Prometheus text exposition format."""
    lines = [
        "# HELP docray_build_info Doc-Ray build information.",
        "# TYPE docray_build_info gauge",
        f'docray_build_info{{version="{_prometheus_label(__version__)}"}} 1',
        "# HELP docray_jobs_by_status Number of jobs by status.",
        "# TYPE docray_jobs_by_status gauge",
    ]
    queue_depth = 0
    for row in rows:
        status = str(row["status"])
        count = int(row["count"])
        lines.append(f'docray_jobs_by_status{{status="{_prometheus_label(status)}"}} {count}')
        if status in {"queued", "running"}:
            queue_depth += count
    lines.extend(
        [
            "# HELP docray_job_queue_depth Jobs currently queued or running.",
            "# TYPE docray_job_queue_depth gauge",
            f"docray_job_queue_depth {queue_depth}",
            "",
        ]
    )
    return "\n".join(lines)


async def metrics_response(pool: AsyncConnectionPool[Any]) -> Response:
    """Collect and render API metrics."""
    async with pool.connection() as conn:
        cur = await conn.execute(JOB_STATUS_METRICS_QUERY)
        rows = await cur.fetchall()
    return Response(format_metrics(list(rows)), media_type="text/plain; version=0.0.4")


def register_metrics_route(app: FastAPI) -> None:
    """Register the Prometheus scrape endpoint on *app*."""

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request) -> Response:
        pool: AsyncConnectionPool[Any] = request.app.state.pool
        return await metrics_response(pool)
