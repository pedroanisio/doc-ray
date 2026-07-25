"""Machine-readable output: the full model state as one JSON document.

Non-finite floats (saturated stations report infinite waits) become ``null``
with the ``saturated`` flags left in place, so consumers never parse
``Infinity`` literals (invalid JSON).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any

from capacity_model import __version__
from capacity_model.analysis import (
    Bottleneck,
    SensitivityRow,
    capacity_margins,
    epistemic_conclusions,
    rank_bottlenecks,
)
from capacity_model.model import ModelResult


def json_safe(value: Any) -> Any:  # noqa: ANN401 - deliberately generic sanitizer
    """Recursively replace non-finite floats with None for strict JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def _tables_obj(res: ModelResult) -> list[dict[str, Any]]:
    """Per-table storage projection rows."""
    return [
        {
            "table": t.name,
            "rows": t.rows,
            "heap_bytes": t.heap_bytes,
            "toast_bytes": t.toast_bytes,
            "index_bytes": t.index_bytes,
            "btree_heights": t.btree_heights,
            "total_bytes": t.total_bytes,
        }
        for t in sorted(res.sizes.tables, key=lambda t: -t.total_bytes)
    ]


def _scenarios_obj(res: ModelResult) -> list[dict[str, Any]]:
    """Per-scenario dynamics + maintenance."""
    out: list[dict[str, Any]] = []
    for rep in res.scenarios:
        d, m = rep.dynamics, rep.maintenance
        out.append(
            {
                "name": rep.scenario.name,
                "description": rep.scenario.description,
                "inputs": asdict(rep.scenario),
                "throughput": {
                    "statements_per_s": d.total_qps,
                    "transactions_per_s": d.total_tps,
                    "read_statements_per_s": d.read_qps,
                    "rows_written_per_s": d.write_rows_s,
                    "commits_per_s": d.commits_s,
                    "wal_bytes_per_s": d.wal_bytes_s,
                },
                "stations": {
                    "cpu": asdict(d.cpu),
                    "disk_read": asdict(d.disk_read),
                    "disk_write_utilization": d.disk_write_util,
                },
                "cache_miss_by_region": d.region_miss,
                "connections": {
                    "api_backends_needed": d.api_backends_needed,
                    "webapp_backends_needed": d.webapp_backends_needed,
                    "worker_backends": d.worker_backends,
                    "total_needed": d.total_backends_needed,
                    "max_connections": d.max_backends_available,
                    "pool_pressure": d.pool_pressure,
                },
                "operations": [asdict(o) for o in d.ops],
                "maintenance": asdict(m),
                "saturation_multiplier": d.saturation_multiplier,
            }
        )
    return out


def build_json_report(
    primary: ModelResult,
    scaled: ModelResult,
    sensitivity: list[SensitivityRow],
    generated_on: str,
) -> dict[str, Any]:
    """Assemble the complete machine-readable report object."""

    def bn(b: Bottleneck) -> dict[str, Any]:
        return asdict(b)

    obj: dict[str, Any] = {
        "meta": {
            "tool": f"doc-ray capacity_model v{__version__}",
            "generated_on": generated_on,
            "target_scale": {
                "users": primary.corpus.users,
                "documents": primary.corpus.documents,
            },
            "traceability_chain": (
                "application behavior -> operation patterns -> relational model "
                "and cardinality -> PostgreSQL resource consumption -> expected "
                "performance and failure thresholds"
            ),
        },
        "assumptions": primary.registry.to_json_obj(),
        "corpus": asdict(primary.corpus),
        "hardware_profiles": {
            "primary": asdict(primary.hardware),
            "scaled": asdict(scaled.hardware),
        },
        "pg_config": {"primary": asdict(primary.pg), "scaled": asdict(scaled.pg)},
        "connection_topology": asdict(primary.topology),
        "storage": {
            "tables": _tables_obj(primary),
            "remainder_bytes": primary.sizes.remainder_bytes,
            "total_bytes": primary.sizes.total_bytes,
            "hot_working_set_bytes": primary.sizes.hot_working_set_bytes,
            "calibration": {
                "model_bytes_per_annotated_sentence": primary.sizes.calibration_model_bps,
                "measured_bytes_per_annotated_sentence": primary.sizes.calibration_measured_bps,
                "ratio": primary.sizes.calibration_ratio,
            },
        },
        "scenarios": {
            "primary": _scenarios_obj(primary),
            "scaled": _scenarios_obj(scaled),
        },
        "bottlenecks": {
            "primary": [bn(b) for b in rank_bottlenecks(primary)],
            "scaled": [bn(b) for b in rank_bottlenecks(scaled)],
        },
        "capacity_margins": {
            "primary": capacity_margins(primary),
            "scaled": capacity_margins(scaled),
        },
        "sensitivity": [asdict(s) for s in sensitivity],
        "epistemic_conclusions": epistemic_conclusions(primary),
    }
    return json_safe(obj)


def dump_json(obj: dict[str, Any]) -> str:
    """Serialize with stable key order for diffable output."""
    return json.dumps(obj, indent=2, sort_keys=False, allow_nan=False)
