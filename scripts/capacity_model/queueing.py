"""Queueing primitives: utilization, Little's law, and M/M/c (Erlang-C) waiting.

The model treats each hardware resource (CPU cores, disk I/O channel) as an
M/M/c station: Poisson arrivals at rate λ, exponential service with mean
``service_s`` per operation, ``c`` parallel servers. This is the standard
first-order analytical treatment; real service-time distributions are less
variable than exponential for index lookups and more variable for large
scans, so per-operation results are tagged as requiring empirical validation
in the report.

Formulas (Kleinrock, *Queueing Systems, Volume 1: Theory*, Wiley, 1975,
ch. 3 — the M/M/m results; also any standard queueing text):

* utilization        ρ = λ · s / c
* Erlang-C           P(wait) = [(cρ)^c / (c!·(1−ρ))] / [Σ_{k<c} (cρ)^k/k! + (cρ)^c/(c!·(1−ρ))]
* mean queue wait    W_q = P(wait) / (c/s − λ)
* wait distribution  P(W_q > t) = P(wait) · e^{−(c/s − λ)·t}  (conditional wait is
  exponential with rate c·μ − λ), which yields closed-form percentiles.

Little's law (L = λ·W) converts between concurrency and rates for the
connection-pool arithmetic in :mod:`capacity_model.dynamics`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class StationResult:
    """Steady-state results for one M/M/c station."""

    name: str
    arrival_rate: float
    service_s: float
    servers: int
    utilization: float
    p_wait: float
    mean_wait_s: float
    mean_response_s: float
    p95_response_s: float
    p99_response_s: float
    saturated: bool


def erlang_c(servers: int, offered_load: float) -> float:
    """Probability an arrival must queue, for offered load ``a = λ·s`` Erlangs.

    Computed with the numerically stable recurrence on the Erlang-B blocking
    probability B(c, a): B(0)=1, B(k) = a·B(k−1) / (k + a·B(k−1)); then
    C = B / (1 − ρ·(1 − B)). Returns 1.0 at or beyond saturation.
    """
    if servers <= 0:
        return 1.0
    rho = offered_load / servers
    if rho >= 1.0:
        return 1.0
    blocking = 1.0
    for k in range(1, servers + 1):
        blocking = offered_load * blocking / (k + offered_load * blocking)
    return blocking / (1.0 - rho * (1.0 - blocking))


def _wait_percentile(p_wait: float, drain_rate: float, percentile: float) -> float:
    """Queue-wait percentile from P(W_q > t) = P(wait)·e^{−drain·t}."""
    tail = 1.0 - percentile
    if p_wait <= tail or drain_rate <= 0.0:
        return 0.0
    return math.log(p_wait / tail) / drain_rate


def mmc_station(name: str, arrival_rate: float, service_s: float, servers: int) -> StationResult:
    """Solve one M/M/c station; saturated stations report infinite waits."""
    offered = arrival_rate * service_s
    servers = max(1, servers)
    rho = offered / servers
    if rho >= 1.0:
        inf = float("inf")
        return StationResult(
            name=name,
            arrival_rate=arrival_rate,
            service_s=service_s,
            servers=servers,
            utilization=rho,
            p_wait=1.0,
            mean_wait_s=inf,
            mean_response_s=inf,
            p95_response_s=inf,
            p99_response_s=inf,
            saturated=True,
        )
    p_wait = erlang_c(servers, offered)
    drain = servers / service_s - arrival_rate if service_s > 0 else float("inf")
    mean_wait = p_wait / drain if drain > 0 else 0.0
    return StationResult(
        name=name,
        arrival_rate=arrival_rate,
        service_s=service_s,
        servers=servers,
        utilization=rho,
        p_wait=p_wait,
        mean_wait_s=mean_wait,
        mean_response_s=mean_wait + service_s,
        p95_response_s=_wait_percentile(p_wait, drain, 0.95) + service_s,
        p99_response_s=_wait_percentile(p_wait, drain, 0.99) + service_s,
        saturated=False,
    )


def concurrency(arrival_rate: float, mean_response_s: float) -> float:
    """Little's law: average number in system L = λ·W."""
    return arrival_rate * mean_response_s
