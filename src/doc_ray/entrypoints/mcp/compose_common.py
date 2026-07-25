"""Compatibility exports for composition primitives moved to the application layer."""

from __future__ import annotations

from doc_ray.application.composition import budget_gap as _budget_gap
from doc_ray.application.composition import budget_passages as _budget_passages
from doc_ray.application.composition import cite as _cite

__all__ = ["_budget_gap", "_budget_passages", "_cite"]
