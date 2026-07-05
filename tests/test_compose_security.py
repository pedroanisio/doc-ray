"""Regression guards for Compose/Postgres deployment safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.api.yml")
_PINNED_PGVECTOR_IMAGE = (
    "pgvector/pgvector:pg16@sha256:ad2e18408bf447f62092a8a5259e7df10505c5a0360bd1a1853ac8b8b0763da2"
)


def _compose(path: str) -> dict[str, Any]:
    return yaml.safe_load((_ROOT / path).read_text(encoding="utf-8"))


def test_postgres_image_is_pinned_to_an_immutable_digest():
    for path in _COMPOSE_FILES:
        postgres = _compose(path)["services"]["postgres"]
        assert postgres["image"] == _PINNED_PGVECTOR_IMAGE


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
