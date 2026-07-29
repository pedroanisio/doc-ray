"""A GPU-free host must have a bring-up path that does not demand a GPU.

`make prod-up` layers the ollama overlays, which give `worker` and `worker-fast`
a hard `depends_on: ollama`. On a host with no NVIDIA device the CDI reservation
cannot resolve, Compose fails with

    Error response from daemon: CDI device injection failed:
    unresolvable CDI devices nvidia.com/gpu=all

and — because the dependency is on the *workers* — both lanes are left in
`Created`, never started. The site keeps serving while nothing drains the queue,
which is the quiet part of the failure.

Observed twice on the production CPU host (2026-07-29). These guards pin the
CPU-only target so the trap cannot come back.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_MAKEFILE = (_ROOT / "Makefile").read_text(encoding="utf-8")


class _TolerantLoader(yaml.SafeLoader):
    """SafeLoader tolerating Compose merge tags (`!reset`, `!override`)."""


for _tag in ("!reset", "!override"):
    _TolerantLoader.add_constructor(_tag, lambda loader, node: None)


def _compose(name: str) -> dict[str, Any]:
    return yaml.load((_ROOT / name).read_text(encoding="utf-8"), Loader=_TolerantLoader)


def _target_body(name: str) -> str:
    """Return the recipe lines of a Make target."""
    match = re.search(rf"^{re.escape(name)}:.*?\n((?:\t.*\n|#.*\n|\t*\n)*)", _MAKEFILE, re.M)
    assert match, f"Make target {name!r} not found"
    return match.group(1)


def _var(name: str) -> str:
    match = re.search(rf"^{re.escape(name)}\s*:?=\s*(.+)$", _MAKEFILE, re.M)
    assert match, f"Make variable {name!r} not found"
    return match.group(1)


def test_the_gpu_overlay_is_what_makes_prod_up_unusable_here() -> None:
    """Documents the cause: the reservation the CPU host cannot satisfy."""
    gpu = _compose("docker-compose.ollama.gpu.yml")
    devices = gpu["services"]["ollama"]["deploy"]["resources"]["reservations"]["devices"]
    assert any("nvidia.com/gpu=all" in str(d.get("device_ids", "")) for d in devices)


def test_the_ollama_overlay_makes_both_worker_lanes_depend_on_the_gpu_service() -> None:
    """Why the failure stops the workers rather than just skipping ollama."""
    ollama = _compose("docker-compose.ollama.yml")
    for service in ("worker", "worker-fast"):
        depends = ollama["services"][service]["depends_on"]
        assert "ollama" in depends, f"{service} must be the reason the lanes stall"


def test_a_cpu_only_bring_up_target_exists() -> None:
    assert "prod-up-cpu:" in _MAKEFILE, (
        "a GPU-free host needs a supported bring-up target; the documented "
        "command must not be one that fails on this hardware"
    )


def test_the_cpu_target_layers_no_gpu_overlay() -> None:
    body = _target_body("prod-up-cpu") + _var("COMPOSE_PROD_CPU")
    assert "ollama.gpu" not in body, "the CPU target must not request a GPU device"
    assert "docker-compose.gpu.yml" not in body, "the CPU target must not reserve a GPU"


def test_the_cpu_target_keeps_the_caption_lane_fencing() -> None:
    """Dropping the ollama overlays must not also drop WORKER_EXCLUDE_KINDS.

    Without the caption overlay a general worker claims visual_caption jobs, which
    is the failure the Makefile comment records for 2026-07-28.
    """
    assert "docker-compose.caption.yml" in _var("COMPOSE_PROD_CPU")


def test_the_cpu_target_starts_every_lane_a_worker_only_host_needs() -> None:
    body = _target_body("prod-up-cpu")
    for service in ("api", "webapp", "worker", "worker-fast", "worker-caption"):
        assert service in body, f"{service} must be started by the CPU bring-up"


def test_the_caption_lane_needs_no_local_model_when_the_vlm_backend_is_hosted() -> None:
    """Why dropping ollama is safe for captions: the backend is an HTTP endpoint."""
    caption = _compose("docker-compose.caption.yml")
    env = caption["services"]["worker-caption"]["environment"]
    assert "DOCRAY_VLM_BACKEND" in env
    assert "DOCRAY_VLM_ENDPOINT" in env
