"""Regression guards for job-kind strings mirrored into Compose."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from doc_ray.domain import jobs

_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE = _ROOT / "docker-compose.yml"
_GPU_WORKER_COMPOSE = _ROOT / "docker-compose.gpuworker.yml"


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose merge tags (e.g. ``!reset``, ``!override``)."""


# Overlay files use Compose's ``!reset`` / ``!override`` merge tags, which the
# stock SafeLoader refuses to construct. We only read plain scalars and maps, so
# resolve any ``!``-prefixed tag to ``None`` rather than aborting the parse.
_ComposeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _compose(path: Path = _COMPOSE) -> dict[str, Any]:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_ComposeLoader)


def _known_job_kinds() -> set[str]:
    return {
        value
        for name, value in vars(jobs).items()
        if name.startswith("KIND_") and isinstance(value, str)
    }


def _kind_timeout_env(kind: str) -> str:
    env_kind = "".join(ch if ch.isalnum() else "_" for ch in kind).upper()
    return f"WORKER_JOB_TIMEOUT_{env_kind}_SECONDS"


def _default_from_compose_expansion(raw: str) -> str:
    match = re.fullmatch(r"\$\{[A-Z0-9_]+:-(.*)\}", raw)
    return match.group(1) if match else raw


def test_worker_fast_excludes_only_known_long_running_job_kinds():
    """Compose worker-fast exclusions must be checked against Python constants."""
    worker_fast_env = _compose()["services"]["worker-fast"]["environment"]

    excluded = set(
        _default_from_compose_expansion(worker_fast_env["WORKER_EXCLUDE_KINDS"]).split(",")
    )

    assert excluded == jobs.LONG_RUNNING_JOB_KINDS
    assert excluded <= _known_job_kinds()


def test_gpu_worker_only_kinds_match_long_running_job_kinds():
    """The worker-ONLY host overlay pins the GPU lane; its default must track Python.

    ``docker-compose.gpuworker.yml`` sets ``WORKER_ONLY_KINDS`` to the long-running
    set so a GPU-only droplet claims exactly that lane — a hand-maintained mirror
    of ``jobs.LONG_RUNNING_JOB_KINDS`` living in a file the base-compose guards
    never read. Parametrizing the guard over it closes drift-map finding H1.
    """
    worker_env = _compose(_GPU_WORKER_COMPOSE)["services"]["worker"]["environment"]

    only = set(_default_from_compose_expansion(worker_env["WORKER_ONLY_KINDS"]).split(","))

    assert only == jobs.LONG_RUNNING_JOB_KINDS
    assert only <= _known_job_kinds()


def test_long_running_job_kinds_have_matching_compose_timeouts():
    """Every long-running kind must have the timeout env var its name implies."""
    worker_env = _compose()["services"]["worker"]["environment"]

    for kind in jobs.LONG_RUNNING_JOB_KINDS:
        env_name = _kind_timeout_env(kind)
        assert env_name in worker_env
        assert worker_env[env_name].startswith(f"${{{env_name}:-")


def test_compose_kind_specific_timeouts_reference_known_job_kinds():
    """A renamed job kind must not leave a stale WORKER_JOB_TIMEOUT_* variable."""
    worker_env = _compose()["services"]["worker"]["environment"]
    known_timeout_envs = {_kind_timeout_env(kind) for kind in _known_job_kinds()}

    configured_timeout_envs = {
        name
        for name in worker_env
        if name.startswith("WORKER_JOB_TIMEOUT_") and name != "WORKER_JOB_TIMEOUT_SECONDS"
    }

    assert configured_timeout_envs <= known_timeout_envs
