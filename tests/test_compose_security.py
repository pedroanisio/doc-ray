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
