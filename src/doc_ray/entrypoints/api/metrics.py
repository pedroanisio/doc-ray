"""Prometheus-compatible metrics for the API."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from fastapi import FastAPI, Request, Response
from psycopg_pool import AsyncConnectionPool

from doc_ray import __version__

JOB_STATUS_METRICS_QUERY = (
    "SELECT status, count(*) AS count FROM jobs GROUP BY status ORDER BY status"
)
JOB_KIND_METRICS_QUERY = """
SELECT kind,
       count(*) FILTER (WHERE status = 'succeeded') AS succeeded_count,
       count(*) FILTER (WHERE status = 'failed') AS failed_count,
       count(*) FILTER (
           WHERE status IN ('succeeded', 'failed', 'canceled')
             AND started_at IS NOT NULL
             AND finished_at IS NOT NULL
       ) AS completed_count,
       coalesce(sum(extract(epoch FROM (finished_at - started_at))) FILTER (
           WHERE status IN ('succeeded', 'failed', 'canceled')
             AND started_at IS NOT NULL
             AND finished_at IS NOT NULL
       ), 0) AS duration_sum_seconds,
       coalesce(max(extract(epoch FROM (finished_at - started_at))) FILTER (
           WHERE status IN ('succeeded', 'failed', 'canceled')
             AND started_at IS NOT NULL
             AND finished_at IS NOT NULL
       ), 0) AS duration_max_seconds
  FROM jobs
 GROUP BY kind
 ORDER BY kind
"""
INGEST_THROUGHPUT_QUERY = """
SELECT count(*) AS count
  FROM jobs
 WHERE kind = 'ingest'
   AND status = 'succeeded'
   AND finished_at >= now() - interval '1 hour'
"""


@dataclass
class _Timing:
    count: int = 0
    total_seconds: float = 0.0
    max_seconds: float = 0.0


class HTTPRequestMetrics:
    """In-process HTTP timing accumulator for scrape-time API metrics."""

    def __init__(self) -> None:
        """Initialize empty request timing buckets."""
        self._graphql: defaultdict[tuple[str, str], _Timing] = defaultdict(_Timing)

    def observe(self, *, path: str, method: str, status_code: int, duration_seconds: float) -> None:
        """Record one HTTP request timing sample."""
        if path != "/graphql":
            return
        key = (method.upper(), str(status_code))
        timing = self._graphql[key]
        timing.count += 1
        timing.total_seconds += duration_seconds
        timing.max_seconds = max(timing.max_seconds, duration_seconds)

    def graphql_rows(self) -> list[dict[str, Any]]:
        """Return GraphQL timing rows in stable label order."""
        return [
            {
                "method": method,
                "status": status,
                "count": timing.count,
                "duration_sum_seconds": timing.total_seconds,
                "duration_max_seconds": timing.max_seconds,
            }
            for (method, status), timing in sorted(self._graphql.items())
        ]


def _prometheus_label(value: object) -> str:
    """Return a Prometheus text-format label value."""
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _metric_number(value: object) -> str:
    """Render numeric metric values without Decimal-specific formatting."""
    if isinstance(value, int):
        return str(value)
    number = float(str(value))
    if number.is_integer():
        return f"{number:.1f}"
    return str(number)


def format_metrics(
    status_rows: list[dict[str, Any]],
    kind_rows: list[dict[str, Any]] | None = None,
    ingest_rows: list[dict[str, Any]] | None = None,
    graphql_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Render API metrics using the Prometheus text exposition format."""
    lines = [
        "# HELP docray_build_info Doc-Ray build information.",
        "# TYPE docray_build_info gauge",
        f'docray_build_info{{version="{_prometheus_label(__version__)}"}} 1',
        "# HELP docray_jobs_by_status Number of jobs by status.",
        "# TYPE docray_jobs_by_status gauge",
    ]
    queue_depth = 0
    for row in status_rows:
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
            "# HELP docray_jobs_completed_total Completed jobs by kind and terminal status.",
            "# TYPE docray_jobs_completed_total counter",
        ]
    )
    for row in kind_rows or []:
        kind = _prometheus_label(row["kind"])
        lines.append(
            f'docray_jobs_completed_total{{kind="{kind}",status="succeeded"}} '
            f"{int(row.get('succeeded_count') or 0)}"
        )
        lines.append(
            f'docray_jobs_completed_total{{kind="{kind}",status="failed"}} '
            f"{int(row.get('failed_count') or 0)}"
        )
    lines.extend(
        [
            "# HELP docray_job_duration_seconds Job processing duration by kind.",
            "# TYPE docray_job_duration_seconds summary",
        ]
    )
    for row in kind_rows or []:
        kind = _prometheus_label(row["kind"])
        lines.append(
            f'docray_job_duration_seconds_count{{kind="{kind}"}} '
            f"{int(row.get('completed_count') or 0)}"
        )
        lines.append(
            f'docray_job_duration_seconds_sum{{kind="{kind}"}} '
            f"{_metric_number(row.get('duration_sum_seconds') or 0)}"
        )
        lines.append(
            f'docray_job_duration_seconds_max{{kind="{kind}"}} '
            f"{_metric_number(row.get('duration_max_seconds') or 0)}"
        )
    ingest_count = int((ingest_rows or [{"count": 0}])[0].get("count") or 0)
    lines.extend(
        [
            "# HELP docray_ingest_throughput_jobs_per_hour "
            "Successful ingest jobs in the last hour.",
            "# TYPE docray_ingest_throughput_jobs_per_hour gauge",
            f"docray_ingest_throughput_jobs_per_hour {ingest_count}",
            "# HELP docray_graphql_request_duration_seconds GraphQL HTTP request duration.",
            "# TYPE docray_graphql_request_duration_seconds summary",
        ]
    )
    for row in graphql_rows or []:
        method = _prometheus_label(row["method"])
        status = _prometheus_label(row["status"])
        labels = f'method="{method}",status="{status}"'
        lines.append(
            f"docray_graphql_request_duration_seconds_count{{{labels}}} "
            f"{int(row.get('count') or 0)}"
        )
        lines.append(
            f"docray_graphql_request_duration_seconds_sum{{{labels}}} "
            f"{_metric_number(row.get('duration_sum_seconds') or 0)}"
        )
        lines.append(
            f"docray_graphql_request_duration_seconds_max{{{labels}}} "
            f"{_metric_number(row.get('duration_max_seconds') or 0)}"
        )
    lines.append("")
    return "\n".join(lines)


async def metrics_response(
    pool: AsyncConnectionPool[Any], request_metrics: HTTPRequestMetrics | None = None
) -> Response:
    """Collect and render API metrics."""
    async with pool.connection() as conn:
        status_cur = await conn.execute(JOB_STATUS_METRICS_QUERY)
        status_rows = await status_cur.fetchall()
        kind_cur = await conn.execute(JOB_KIND_METRICS_QUERY)
        kind_rows = await kind_cur.fetchall()
        ingest_cur = await conn.execute(INGEST_THROUGHPUT_QUERY)
        ingest_rows = await ingest_cur.fetchall()
    graphql_rows = request_metrics.graphql_rows() if request_metrics is not None else []
    return Response(
        format_metrics(
            list(status_rows),
            list(kind_rows),
            list(ingest_rows),
            graphql_rows,
        ),
        media_type="text/plain; version=0.0.4",
    )


def register_metrics_route(app: FastAPI) -> None:
    """Register the Prometheus scrape endpoint on *app*."""
    request_metrics = HTTPRequestMetrics()
    app.state.http_request_metrics = request_metrics

    @app.middleware("http")
    async def metrics_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started_at = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            request_metrics.observe(
                path=request.url.path,
                method=request.method,
                status_code=status_code,
                duration_seconds=perf_counter() - started_at,
            )

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request) -> Response:
        pool: AsyncConnectionPool[Any] = request.app.state.pool
        metrics: HTTPRequestMetrics = request.app.state.http_request_metrics
        return await metrics_response(pool, request_metrics=metrics)
