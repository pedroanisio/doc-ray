"""Regression guards for Compose/Postgres deployment safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.api.yml")
_DOCKERFILE = _ROOT / "Dockerfile"
_PINNED_PGVECTOR_IMAGE = (
    "pgvector/pgvector:pg16@sha256:ad2e18408bf447f62092a8a5259e7df10505c5a0360bd1a1853ac8b8b0763da2"
)
_PINNED_PYTHON_BASE_IMAGE = (
    "python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf"
)


def _compose(path: str) -> dict[str, Any]:
    return yaml.safe_load((_ROOT / path).read_text(encoding="utf-8"))


def test_postgres_image_is_pinned_to_an_immutable_digest():
    for path in _COMPOSE_FILES:
        postgres = _compose(path)["services"]["postgres"]
        assert postgres["image"] == _PINNED_PGVECTOR_IMAGE


def test_app_base_image_is_pinned_to_an_immutable_digest():
    """BL-111: every FROM in the app Dockerfile carries a @sha256: digest.

    BL-039 pinned the Postgres image; this extends the same
    reproducibility/supply-chain guard to the image every app service
    (api, worker, migrate, ingester, backup-scheduler, webapp, ...) builds from.
    """
    images = [
        line.split()[1]
        for line in _DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("FROM ")
    ]
    assert images, "Dockerfile has no FROM instruction"
    for image in images:
        assert "@sha256:" in image, f"unpinned base image: {image}"
    assert _PINNED_PYTHON_BASE_IMAGE in images


def test_dockerfile_drops_root_before_the_entrypoint():
    """BL-112: parsing untrusted uploads (PDF/EPUB/DOCX) must not run as root.

    The final runtime stage must switch to a non-root user before any
    entrypoint runs, so every service built from this image (api, worker,
    migrate, ingester, backup-scheduler, webapp, nginx-certgen) executes
    unprivileged.
    """
    lines = [line.strip() for line in _DOCKERFILE.read_text(encoding="utf-8").splitlines()]
    user_lines = [(i, line) for i, line in enumerate(lines) if line.startswith("USER ")]
    entrypoint_at = next(i for i, line in enumerate(lines) if line.startswith("ENTRYPOINT "))

    assert user_lines, "Dockerfile never drops root (no USER instruction)"
    last_user_at, last_user = user_lines[-1]
    assert last_user.split()[1] not in {"root", "0"}, "final USER must be non-root"
    assert last_user_at < entrypoint_at, "USER must take effect before the ENTRYPOINT"


def test_postgres_port_binds_to_loopback_by_default():
    for path in _COMPOSE_FILES:
        postgres = _compose(path)["services"]["postgres"]
        assert postgres["ports"] == ["127.0.0.1:${POSTGRES_PORT:-5432}:5432"]


def test_compose_requires_a_non_default_postgres_password():
    for path in _COMPOSE_FILES:
        postgres = _compose(path)["services"]["postgres"]
        password = postgres["environment"]["POSTGRES_PASSWORD"]
        guard = " ".join(postgres["entrypoint"])
        assert password.startswith("${POSTGRES_PASSWORD:?")
        assert "docray" in guard
        assert "change-me" in guard


def test_env_example_does_not_publish_the_historical_default_password():
    text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=docray" not in text
    assert "POSTGRES_PASSWORD=change-me" in text


def test_ingester_is_profiled_as_an_on_demand_run_only_service():
    compose = _compose("docker-compose.yml")
    ingester = compose["services"]["ingester"]

    assert ingester["profiles"] == ["ingest"]
    assert "restart" not in ingester


def test_db_health_monitor_is_an_opt_in_ops_profile_alert_path():
    compose = _compose("docker-compose.yml")
    monitor = compose["services"]["db-health-monitor"]

    assert monitor["profiles"] == ["ops"]
    assert monitor["restart"] == "unless-stopped"
    assert monitor["entrypoint"] == [
        "python",
        "-m",
        "doc_ray.entrypoints.db_health",
        "--watch",
        "${DOCRAY_DB_HEALTH_INTERVAL_SECONDS:-300}",
    ]


def test_backup_scheduler_runs_with_the_default_stack():
    compose = _compose("docker-compose.yml")
    scheduler = compose["services"]["backup-scheduler"]

    assert "profiles" not in scheduler
    assert scheduler["restart"] == "unless-stopped"
    assert scheduler["entrypoint"] == ["python", "-m", "doc_ray.entrypoints.backup_scheduler"]
    assert scheduler["environment"]["DOCRAY_BACKUP_SCHEDULE_SECONDS"] == (
        "${DOCRAY_BACKUP_SCHEDULE_SECONDS:-86400}"
    )
