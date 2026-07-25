"""Analytical PostgreSQL capacity model for the doc-ray corpus at target scale.

This package builds a *quantitative capacity model* — not a load generator —
for the doc-ray PostgreSQL database at a target scale of ~10,000 registered
users and ~1,000,000 corpus documents. It chains five explicit layers, each
traceable to its inputs:

    application behavior  →  operation patterns  →  relational model and
    cardinality  →  PostgreSQL resource consumption  →  expected performance
    and failure thresholds.

Layer map (one module per layer, plus shared primitives):

``assumptions``
    Typed, sourced, epistemically-tagged inputs. Every number the model uses
    is an :class:`~capacity_model.assumptions.Assumption` carrying its source
    (repo file:line, PostgreSQL documentation URL, or an explicit conservative
    assumption) and whether it is analytical or needs empirical validation.
``pg_constants``
    Documented PostgreSQL storage-format and default-configuration constants.
``storage``
    Heap / B-tree / GIN / TOAST size estimators built on the storage format.
``queueing``
    Little's law, M/M/c (Erlang-C) waiting-time, and utilization primitives.
``schema_catalog``
    The doc-ray tables, indexes, and cardinality ratios derived from
    ``db/migrations/*.sql`` and ``docs/``.
``workload``
    User-behavior → operation-mix scenarios (baseline / expected / peak /
    degraded) mapped onto concrete query shapes.
``resources``
    Hardware and PostgreSQL configuration profiles.
``sizing``
    Table/index/TOAST/WAL storage projections at target scale.
``dynamics``
    Throughput, per-operation service time, latency, connections.
``maintenance``
    Dead tuples, autovacuum cadence, WAL volume, checkpoint pressure,
    replication overhead.
``analysis``
    Bottleneck ranking, saturation multipliers, sensitivity of results to
    assumptions, capacity margins.
``report``
    Machine-readable JSON and human-readable Markdown emission.

Run from the repo root::

    python -m capacity_model --help          # with scripts/ on PYTHONPATH
    python scripts/run_capacity_model.py     # thin wrapper, no PYTHONPATH needed
"""

from __future__ import annotations

__version__ = "1.0.0"
