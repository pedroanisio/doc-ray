"""Dependency probes behind the API's readiness endpoint.

Liveness (the process is up) is cheap and unconditional. Readiness is not: the API
can be *running* yet unable to reach Postgres or the object store — the exact latent
failure that made ``ingestDocument`` blow up at upload time with a cryptic boto3
``EndpointConnectionError`` when the container was started without the right ``S3_*``
env. These probes turn that silent misconfiguration into an explicit, queryable signal
(``/readyz`` -> HTTP 503, an ``unhealthy`` Docker healthcheck) so a broken deployment is
noticed at the orchestration layer instead of at the first user upload.

Each probe is total: it never raises, it maps success to ``"ok"`` and any failure to a
short diagnostic string, because a health check that can crash is not a health check.
"""

from __future__ import annotations

from typing import Any, Protocol

from psycopg_pool import AsyncConnectionPool

OK = "ok"


class Pingable(Protocol):
    """The slice of the object store the readiness probe needs: a reachability ping."""

    def ping(self) -> None:
        """Confirm the store answers; raise on unreachable endpoint or bad credentials."""
        ...


async def probe_database(pool: AsyncConnectionPool[Any]) -> str:
    """Return ``"ok"`` if a pooled connection round-trips ``SELECT 1``, else the error."""
    try:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:  # any failure is a readiness signal, never a crash
        return f"error: {type(exc).__name__}: {exc}"
    return OK


def probe_object_store(store: Pingable) -> str:
    """Return ``"ok"`` if the object store answers a reachability probe, else why not.

    Blocking (boto3); call off the event loop via ``anyio.to_thread.run_sync``.
    """
    try:
        store.ping()
    except Exception as exc:
        return f"unreachable: {type(exc).__name__}: {exc}"
    return OK


def readiness(checks: dict[str, str]) -> dict[str, Any]:
    """Assemble a readiness report from named checks; ready iff every check is ``"ok"``."""
    ready = all(value == OK for value in checks.values())
    return {"status": "ready" if ready else "not_ready", "ready": ready, "checks": checks}
