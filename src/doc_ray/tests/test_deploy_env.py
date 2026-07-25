"""Tests for environment-aware compose selection (``scripts/deploy_env.py``).

The heavy workers (transformer annotation, sentence embedding) run far faster on
a GPU, but the base stack must boot on any host. ``deploy_env`` assesses the
environment and picks the compose file set + GPU worker scale accordingly. The
DECISION is pure and pinned here; the Docker PROBE (``detect_gpu``) is
environment-dependent and exercised only for its shape.
"""

from __future__ import annotations

import deploy_env


def test_compose_files_cpu_host_uses_base_only() -> None:
    """A CPU-only host selects the base files unchanged — no GPU overlay."""
    assert deploy_env.compose_files(gpu_available=False) == [
        "docker-compose.yml",
        "docker-compose.mcp.yml",
    ]


def test_compose_files_gpu_host_layers_the_overlay_last() -> None:
    """A GPU host layers ``docker-compose.gpu.yml`` on top (last wins in compose)."""
    files = deploy_env.compose_files(gpu_available=True)
    assert files == [
        "docker-compose.yml",
        "docker-compose.mcp.yml",
        "docker-compose.gpu.yml",
    ]
    assert files[-1] == "docker-compose.gpu.yml", "the GPU overlay must be applied last"


def test_worker_scale_pins_single_replica_on_gpu() -> None:
    """An 8 GB consumer card fits exactly one heavy GPU worker (two OOM)."""
    assert deploy_env.worker_scale(gpu_available=True) == 1


def test_worker_scale_uncapped_on_cpu() -> None:
    """CPU-only imposes no VRAM cap — leave the compose default (None)."""
    assert deploy_env.worker_scale(gpu_available=False) is None


def test_detect_gpu_returns_bool() -> None:
    """The probe is side-effecting but must always yield a plain bool."""
    assert isinstance(deploy_env.detect_gpu(), bool)
