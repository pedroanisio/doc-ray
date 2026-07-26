#!/usr/bin/env python3
"""Environment-aware Docker Compose selection: GPU workers when possible, CPU otherwise.

doc-ray's base stack is CPU-only so it boots on any host. But its heavy workers —
transformer annotation (``en_core_web_trf``) and sentence embedding
(``all-mpnet-base-v2``) — run an order of magnitude faster on a GPU. This module
*assesses the runtime environment*: when an NVIDIA GPU is actually exposed to
containers, it layers ``docker-compose.gpu.yml`` on top of the base files and pins
the GPU ``worker`` pool to a single replica (an 8 GB consumer card fits exactly one
heavy GPU worker — two OOM; the CPU ``worker-fast`` pool keeps draining light jobs
in parallel). On a CPU-only host it selects the base files unchanged.

The intent is a hybrid fleet without hand-editing ``-f`` flags per host:

    docker compose $(python scripts/deploy_env.py --compose-args) \
        up -d $(python scripts/deploy_env.py --up-args)

Design: the DECISION (:func:`compose_files`, :func:`worker_scale`) is pure and
unit-tested; the PROBE (:func:`detect_gpu`) shells out to Docker and is isolated so
the decision stays testable on any host (including CI with no GPU).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

#: Base compose file set for the running ``doc-ray`` project (API + MCP + workers).
BASE_FILES = ["docker-compose.yml", "docker-compose.mcp.yml"]
#: Overlay that reserves the host NVIDIA GPU (via CDI) for the ``worker`` pool.
GPU_OVERLAY = "docker-compose.gpu.yml"
#: A minimal CUDA base image used only to prove a container can see the GPU.
_PROBE_IMAGE = "nvidia/cuda:12.4.0-base-ubuntu22.04"

#: Set this to run the local stack anyway on a host the guard flags. Deliberate
#: local-infra work on a production box stays possible — it just cannot happen
#: by reflex.
OVERRIDE_ENV_VAR = "DOCRAY_ALLOW_LOCAL_STACK"
#: The production env file ENVIRONMENT.md prescribes (``ln -sf .env.prod .env``).
_PROD_ENV_BASENAME = ".env.prod"
#: Hosts the base compose can actually be serving a Postgres on.
_LOCAL_DB_HOSTS = frozenset(
    {"postgres", "localhost", "127.0.0.1", "::1", "[::1]", "host.docker.internal"}
)


def compose_files(*, gpu_available: bool) -> list[str]:
    """Return the ordered ``-f`` compose file set for this environment.

    The GPU overlay is appended LAST so its worker device reservation wins under
    Docker Compose's last-file-wins merge. A CPU-only host gets the base set only.
    """
    files = list(BASE_FILES)
    if gpu_available:
        files.append(GPU_OVERLAY)
    return files


def worker_scale(gpu_available: bool) -> int | None:
    """Return the ``worker`` replica count, or ``None`` to keep the compose default.

    An 8 GB consumer GPU fits exactly one heavy GPU worker (torch + cupy hold a
    growing device pool; two replicas OOM), so a GPU host pins ``worker=1``. A
    CPU-only host has no such VRAM cap, so we leave the compose file's own default.
    """
    return 1 if gpu_available else None


def detect_gpu(*, probe_image: str = _PROBE_IMAGE) -> bool:
    """Return True iff Docker can actually run a container that sees an NVIDIA GPU.

    This is stronger than "a GPU exists on the host": it confirms the container
    toolchain (nvidia-container-toolkit / CDI) is wired up, which is what the
    ``deploy.resources`` reservation in the overlay needs. Any failure — no
    ``docker``, no driver, no toolkit — is treated as "no GPU" (fall back to CPU).
    """
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
            [docker, "run", "--rm", "--gpus", "all", probe_image, "nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "GPU" in result.stdout


def production_env_conflict(env_path: Path) -> str | None:
    """Return why *env_path* drives a production deployment, or ``None`` if local.

    The local stack and the managed deployment share the ``doc-ray`` compose
    project name, so bringing the local stack up on a production host does not
    stand a second stack beside the first — it *recreates the running one*
    against a throwaway local Postgres. That is what happened on 2026-07-26: the
    public site went down for about five minutes and nothing in the tooling
    objected.

    Two independent signals, because a host may carry either:

    * ``.env`` resolves to a file named ``.env.prod`` — the layout ENVIRONMENT.md
      prescribes for the production box (``ln -sf .env.prod .env``).
    * ``DATABASE_URL`` names a host the base compose cannot be serving. The base
      stack only ever offers ``postgres`` on the compose network (or a loopback
      Postgres on the host network), so anything else is a managed cluster.

    Pure: reads the file, touches no Docker and no network, so the decision stays
    testable on any host.
    """
    try:
        resolved = env_path.resolve()
    except OSError:  # pragma: no cover - unreadable path behaves as absent
        return None
    if not resolved.is_file():
        return None

    if resolved.name.startswith(_PROD_ENV_BASENAME):
        return f"{env_path} resolves to {resolved.name}"

    host = _database_host(resolved)
    if host is not None and host not in _LOCAL_DB_HOSTS:
        return f"{env_path} points DATABASE_URL at {host}"
    return None


def _database_host(env_path: Path) -> str | None:
    """Return the host in the file's ``DATABASE_URL``, or ``None`` if unset."""
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("DATABASE_URL="):
            continue
        value = line.split("=", 1)[1].strip().strip("'\"")
        netloc = urlsplit(value).netloc
        # Strip credentials, then the port; IPv6 literals keep their brackets.
        hostport = netloc.rsplit("@", 1)[-1]
        if hostport.startswith("["):
            return hostport.partition("]")[0] + "]"
        return hostport.rsplit(":", 1)[0] or None
    return None


def _print_compose_args(gpu_available: bool) -> None:
    """Print ``-f <file> ...`` for ``docker compose`` (consumed by the Makefile)."""
    parts: list[str] = []
    for f in compose_files(gpu_available=gpu_available):
        parts += ["-f", f]
    print(" ".join(parts))


def _print_up_args(gpu_available: bool) -> None:
    """Print extra ``up`` args (GPU worker scale), or nothing on a CPU host."""
    scale = worker_scale(gpu_available)
    print(f"--scale worker={scale}" if scale is not None else "")


def _check_local_env(env_path: Path) -> int:
    """Gate for the local-stack targets: refuse on a production host."""
    if os.environ.get(OVERRIDE_ENV_VAR):
        return 0
    reason = production_env_conflict(env_path)
    if reason is None:
        return 0
    print(
        f"refusing to run the LOCAL stack: {reason}.\n"
        "\n"
        "The local stack and the managed deployment share the 'doc-ray' compose\n"
        "project, so this would recreate the RUNNING production containers against\n"
        "a throwaway local Postgres and take the public site down with them.\n"
        "\n"
        "  production:  make prod-ollama-up   (managed cluster + Spaces)\n"
        f"  anyway:      {OVERRIDE_ENV_VAR}=1 make up\n",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    """CLI: assess the environment and emit compose args, or report the verdict."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-args", action="store_true", help="print '-f <file> ...'")
    parser.add_argument("--up-args", action="store_true", help="print extra 'up' args")
    parser.add_argument("--detect", action="store_true", help="exit 0 iff a GPU is usable")
    parser.add_argument(
        "--check-local-env",
        action="store_true",
        help="exit non-zero if this host's .env drives a production deployment",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="env file the local stack would use (default: .env)",
    )
    parser.add_argument(
        "--assume-gpu",
        choices=["auto", "yes", "no"],
        default="auto",
        help="override the probe (default: auto = run the Docker probe)",
    )
    args = parser.parse_args(argv)

    if args.check_local_env:
        return _check_local_env(Path(args.env_file))

    gpu = detect_gpu() if args.assume_gpu == "auto" else args.assume_gpu == "yes"

    if args.detect:
        print("gpu" if gpu else "cpu", file=sys.stderr)
        return 0 if gpu else 1
    if args.compose_args:
        _print_compose_args(gpu)
        return 0
    if args.up_args:
        _print_up_args(gpu)
        return 0
    # Default: a human-readable summary.
    print(f"environment: {'GPU' if gpu else 'CPU-only'}", file=sys.stderr)
    print(f"compose files: {' '.join(compose_files(gpu_available=gpu))}", file=sys.stderr)
    scale = worker_scale(gpu)
    print(f"worker scale: {scale if scale is not None else '(compose default)'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
