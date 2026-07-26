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


# --------------------------------------------------------------------------
# Production-env guard: `make up` must refuse to hijack a production host.
# --------------------------------------------------------------------------
#
# The incident this pins (2026-07-26): `make up` — the LOCAL stack target — was
# run on the production host. There, `.env` is a symlink to `.env.prod` and the
# compose project name is the same `doc-ray` production runs under, so the base
# compose recreated api/webapp/worker/worker-fast/mcp against a brand-new local
# Postgres and left them stopped when `migrate` lost a startup race. The public
# site was down for ~5 minutes. Nothing in the tooling objected.


def test_env_is_production_detects_the_prod_symlink(tmp_path) -> None:
    """The documented production layout: `.env` -> `.env.prod`."""
    prod = tmp_path / ".env.prod"
    prod.write_text("DATABASE_URL=postgresql://u:p@managed.example.com:25060/docray\n")
    env = tmp_path / ".env"
    env.symlink_to(prod)

    reason = deploy_env.production_env_conflict(env)

    assert reason is not None
    assert ".env.prod" in reason


def test_env_is_production_detects_a_remote_database_host(tmp_path) -> None:
    """A host without the symlink convention is caught by where the data lives.

    The base compose only ever serves a `postgres` service on the compose
    network; a DATABASE_URL pointing anywhere else means this env file drives a
    managed deployment, whatever it is named.
    """
    env = tmp_path / ".env"
    env.write_text(
        "DATABASE_URL=postgresql://u:p@db-pgsql-tor1-55041.h.db.ondigitalocean.com:25060/docray\n"
    )

    reason = deploy_env.production_env_conflict(env)

    assert reason is not None
    assert "ondigitalocean.com" in reason


def test_a_genuine_local_env_is_allowed(tmp_path) -> None:
    """The ordinary development case must stay frictionless."""
    env = tmp_path / ".env"
    env.write_text(
        "DATABASE_URL=postgresql://docray:docray@postgres:5432/docray\nS3_BUCKET=documents\n"
    )

    assert deploy_env.production_env_conflict(env) is None


def test_a_missing_env_file_is_allowed(tmp_path) -> None:
    """No `.env` at all is a fresh checkout, not a production host."""
    assert deploy_env.production_env_conflict(tmp_path / ".env") is None


def test_localhost_database_is_allowed(tmp_path) -> None:
    """A host-network dev Postgres is still local."""
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://docray:docray@127.0.0.1:5432/docray\n")

    assert deploy_env.production_env_conflict(env) is None


def test_check_local_env_cli_fails_on_a_production_host(tmp_path, capsys) -> None:
    """The gate `make up` calls: non-zero exit, and it names the escape hatch."""
    prod = tmp_path / ".env.prod"
    prod.write_text("DATABASE_URL=postgresql://u:p@managed.example.com:25060/docray\n")
    (tmp_path / ".env").symlink_to(prod)

    code = deploy_env.main(["--check-local-env", "--env-file", str(tmp_path / ".env")])

    assert code != 0
    err = capsys.readouterr().err
    assert "prod" in err.lower()
    assert deploy_env.OVERRIDE_ENV_VAR in err


def test_check_local_env_cli_passes_on_a_development_host(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("DATABASE_URL=postgresql://docray:docray@postgres:5432/docray\n")

    assert deploy_env.main(["--check-local-env", "--env-file", str(env)]) == 0


def test_override_env_var_lets_an_operator_proceed(tmp_path, monkeypatch, capsys) -> None:
    """Deliberate local-infra runs on a prod box stay possible, but must be explicit."""
    prod = tmp_path / ".env.prod"
    prod.write_text("DATABASE_URL=postgresql://u:p@managed.example.com:25060/docray\n")
    (tmp_path / ".env").symlink_to(prod)
    monkeypatch.setenv(deploy_env.OVERRIDE_ENV_VAR, "1")

    assert deploy_env.main(["--check-local-env", "--env-file", str(tmp_path / ".env")]) == 0
