"""Regression guards for Compose/Postgres deployment safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.api.yml")
_TEST_COMPOSE_FILE = "docker-compose.test.yml"
_DOCKERFILE = _ROOT / "Dockerfile"
_TEST_DOCKERFILE = _ROOT / "docker" / "test" / "Dockerfile"
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


def test_webapp_rate_limit_knobs_are_exposed_in_compose_and_env_example():
    """BL-105: operators can tune expensive webapp route throttles in Compose."""
    compose_env = _compose("docker-compose.yml")["services"]["webapp"]["environment"]
    env_text = (_ROOT / ".env.example").read_text(encoding="utf-8")

    expected = {
        "DOCRAY_WEBAPP_SEARCH_RATE_LIMIT_REQUESTS": "${DOCRAY_WEBAPP_SEARCH_RATE_LIMIT_REQUESTS:-60}",
        "DOCRAY_WEBAPP_SEARCH_RATE_LIMIT_WINDOW_SECONDS": (
            "${DOCRAY_WEBAPP_SEARCH_RATE_LIMIT_WINDOW_SECONDS:-60}"
        ),
        "DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT_REQUESTS": "${DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT_REQUESTS:-10}",
        "DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT_WINDOW_SECONDS": (
            "${DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT_WINDOW_SECONDS:-60}"
        ),
    }

    for key, value in expected.items():
        assert compose_env[key] == value
        assert f"{key}=" in env_text


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


def test_worker_timeout_knobs_are_documented_and_passed_to_compose():
    compose = _compose("docker-compose.yml")
    worker_env = compose["services"]["worker"]["environment"]
    env_text = (_ROOT / ".env.example").read_text(encoding="utf-8")

    assert worker_env["WORKER_JOB_TIMEOUT_SECONDS"] == "${WORKER_JOB_TIMEOUT_SECONDS:-3600}"
    assert worker_env["DOCRAY_RESOLVER_TIMEOUT_SECONDS"] == (
        "${DOCRAY_RESOLVER_TIMEOUT_SECONDS:-60}"
    )
    assert "WORKER_JOB_TIMEOUT_SECONDS=3600" in env_text
    assert "DOCRAY_RESOLVER_TIMEOUT_SECONDS=60" in env_text


def test_test_compose_stack_uses_dedicated_state_and_names():
    compose = _compose(_TEST_COMPOSE_FILE)
    services = compose["services"]

    assert compose["name"] == "docray-test"
    assert set(services) == {
        "test-postgres",
        "test-minio",
        "test-createbucket",
        "test-migrate",
        "test-runner",
    }
    assert services["test-postgres"]["container_name"] == "docray-test-postgres"
    assert services["test-minio"]["container_name"] == "docray-test-minio"
    assert services["test-postgres"]["ports"] == [
        "127.0.0.1:${TEST_POSTGRES_PORT:-15432}:5432"
    ]
    assert services["test-minio"]["ports"] == [
        "127.0.0.1:${TEST_MINIO_PORT:-19000}:9000",
        "127.0.0.1:${TEST_MINIO_CONSOLE_PORT:-19001}:9001",
    ]
    assert services["test-postgres"]["volumes"][0] == "pgdata-test:/var/lib/postgresql/data"
    assert services["test-minio"]["volumes"] == ["miniodata-test:/data"]
    assert set(compose["volumes"]) == {"pgdata-test", "miniodata-test"}


def test_test_compose_stack_uses_a_dedicated_network():
    compose = _compose(_TEST_COMPOSE_FILE)
    services = compose["services"]

    assert set(compose["networks"]) == {"test-net"}
    assert compose["networks"]["test-net"]["name"] == "docray-test-net"
    assert compose["networks"]["test-net"]["driver"] == "bridge"
    for service in services.values():
        assert service["networks"] == ["test-net"]


def test_test_compose_does_not_reuse_uat_credentials_or_database_urls():
    compose = _compose(_TEST_COMPOSE_FILE)
    postgres_env = compose["services"]["test-postgres"]["environment"]
    runner_env = compose["services"]["test-runner"]["environment"]

    assert postgres_env["POSTGRES_USER"] == "${TEST_POSTGRES_USER:-docray_test}"
    assert postgres_env["POSTGRES_PASSWORD"].startswith("${TEST_POSTGRES_PASSWORD:?")
    assert postgres_env["POSTGRES_DB"] == "${TEST_POSTGRES_DB:-docray_test}"
    assert "test-postgres:5432/${TEST_POSTGRES_DB:-docray_test}" in runner_env["DATABASE_URL"]
    assert "postgres:5432/${POSTGRES_DB:-docray}" not in runner_env["DATABASE_URL"]
    assert runner_env["S3_ENDPOINT"] == "http://test-minio:9000"


def test_test_environment_example_and_script_are_test_stack_only():
    env_text = (_ROOT / ".env.test.example").read_text(encoding="utf-8")
    script_text = (_ROOT / "scripts" / "test-in-docker.sh").read_text(encoding="utf-8")
    gitignore_text = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    env_lines = env_text.splitlines()

    assert "TEST_POSTGRES_PASSWORD=" in env_text
    assert "TEST_POSTGRES_PORT=15432" in env_text
    assert not any(line.startswith("POSTGRES_PASSWORD=") for line in env_lines)
    assert not any(line.startswith("DATABASE_URL=") for line in env_lines)
    assert "docker-compose.test.yml" in script_text
    assert ".env.test" in script_text
    assert "--project-name" in script_text and "docray-test" in script_text
    assert "docker-compose.yml" not in script_text
    assert ".env.test" in gitignore_text


def test_dockerignore_excludes_local_test_build_noise():
    ignored = {
        line.strip()
        for line in (_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert ".venv" in ignored
    assert ".venv-proto/" in ignored
    assert ".pytest_cache/" in ignored
    assert ".agent-tasks/" in ignored
    assert "backups/" in ignored
    assert "static/*" in ignored
    assert "!static/fixtures/" in ignored
    assert "!static/fixtures/**" in ignored


def test_test_runner_writes_pytest_and_coverage_outputs_to_container_tmp():
    runner = _compose(_TEST_COMPOSE_FILE)["services"]["test-runner"]

    assert runner["environment"]["COVERAGE_FILE"] == "/tmp/docray-test.coverage"
    assert "cache_dir=/tmp/docray-pytest-cache" in runner["command"][0]


def test_test_runner_builds_from_a_dedicated_self_contained_test_image():
    runner = _compose(_TEST_COMPOSE_FILE)["services"]["test-runner"]

    assert runner["build"] == {"context": ".", "dockerfile": "docker/test/Dockerfile"}
    assert "volumes" not in runner


def test_test_migrate_uses_the_dedicated_test_image_not_the_uat_app_image():
    migrate = _compose(_TEST_COMPOSE_FILE)["services"]["test-migrate"]

    assert migrate["build"] == {"context": ".", "dockerfile": "docker/test/Dockerfile"}
    assert "volumes" not in migrate


def test_test_dockerfile_copies_sources_tests_and_migrations_into_the_image():
    text = _TEST_DOCKERFILE.read_text(encoding="utf-8")

    assert "apt-get install -y --no-install-recommends" in text
    assert "tesseract-ocr" in text
    assert "tesseract-ocr-eng" in text
    assert "COPY Dockerfile docker-compose.yml docker-compose.api.yml docker-compose.test.yml ./" in text
    assert "COPY .dockerignore .env.example .env.test.example .gitignore ./" in text
    assert "COPY .pre-commit-config.yaml CLAUDE.md ./" in text
    assert "COPY .github ./.github" in text
    assert "COPY README.md CHANGELOG.md engineering_decisions.yml uv.lock ./" in text
    assert "COPY src ./src" in text
    assert "COPY tests ./tests" in text
    assert "COPY scripts ./scripts" in text
    assert "COPY static/fixtures ./static/fixtures" in text
    assert "COPY webapp ./webapp" in text
    assert "COPY db ./db" in text
    assert "COPY docs ./docs" in text
    assert "COPY docker ./docker" in text
    assert "COPY nginx ./nginx" in text
