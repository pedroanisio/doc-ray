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


class _TolerantLoader(yaml.SafeLoader):
    """SafeLoader that tolerates Compose merge tags (`!reset`, `!override`).

    The prod overlay uses `depends_on: !reset {}` to sever a base dependency;
    yaml.safe_load raises on the unknown tag, so parse the tagged node as its
    plain value (here `!reset {}` -> `{}`) instead.
    """


def _ignore_compose_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


_TolerantLoader.add_multi_constructor("!", _ignore_compose_tag)


def _compose_tolerant(path: str) -> dict[str, Any]:
    return yaml.load((_ROOT / path).read_text(encoding="utf-8"), Loader=_TolerantLoader)


_PROD_OVERLAY = "docker-compose.prod.yml"
_PGADMIN_OVERLAY = "docker-compose.pgadmin.yml"
#: App services the prod overlay must repoint at the managed cluster + Spaces.
_PROD_APP_SERVICES = (
    "migrate",
    "api",
    "worker",
    "worker-fast",
    "backup-scheduler",
    "webapp",
    "db-health-monitor",
)
#: Local infra the prod overlay must gate out of a bare managed `up`.
_PROD_LOCAL_INFRA = ("postgres", "minio", "createbucket")


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


def test_makefile_exposes_default_three_worker_ops_path():
    """Long document jobs must not depend on a tribal --scale incantation."""
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "DOCRAY_WORKERS ?= 3" in makefile
    assert "workers-up:" in makefile
    assert "docker compose up -d --scale worker=$(DOCRAY_WORKERS) worker" in makefile
    assert "workers-status:" in makefile
    assert "docker compose ps worker" in makefile


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
    assert services["test-postgres"]["ports"] == ["127.0.0.1:${TEST_POSTGRES_PORT:-15432}:5432"]
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
    # Glob so every compose overlay (prod, cloudflared, ollama, pgadmin, ...) is
    # in the test image — the compose-security guards read them, and a bare list
    # silently dropped new overlays (FileNotFound in the isolated run).
    assert "COPY Dockerfile docker-compose*.yml ./" in text
    assert (
        "COPY .dockerignore .env.example .env.prod.example .env.test.example .gitignore ./" in text
    )
    assert "COPY .pre-commit-config.yaml CLAUDE.md ./" in text
    assert "COPY .github ./.github" in text
    assert "COPY README.md CHANGELOG.md engineering_decisions.yml uv.lock Makefile ./" in text
    assert "COPY src ./src" in text
    assert "COPY tests ./tests" in text
    assert "COPY scripts ./scripts" in text
    assert "COPY static/fixtures ./static/fixtures" in text
    assert "COPY webapp ./webapp" in text
    assert "COPY db ./db" in text
    assert "COPY docs ./docs" in text
    assert "COPY docker ./docker" in text
    assert "COPY nginx ./nginx" in text


def _grace_seconds(value: str) -> float:
    """Parse a Compose duration ('120s', '2m', '1m30s', bare seconds) to seconds."""
    text = str(value).strip()
    if not text:
        return 0.0
    units = {"h": 3600.0, "m": 60.0, "s": 1.0}
    total, number = 0.0, ""
    for ch in text:
        if ch.isdigit() or ch == ".":
            number += ch
        elif ch in units and number:
            total += float(number) * units[ch]
            number = ""
        else:
            raise ValueError(f"unparseable Compose duration: {value!r}")
    if number:  # bare number = seconds
        total += float(number)
    return total


def test_worker_has_a_real_drain_window_before_sigkill():
    """The worker's stop_grace_period must stay well above Docker's 10s default.

    Deploy churn with the 10s default killed long annotate jobs mid-run and
    exhausted their max_attempts with no code failure (the 2026-07-12
    incident); the drain-release design assumes SIGTERM gives the in-flight
    job a real window to finish or stamp drain_requested_at.
    """
    worker = _compose("docker-compose.yml")["services"]["worker"]
    grace = worker.get("stop_grace_period")
    assert grace is not None, (
        "docker-compose.yml's worker service lost its stop_grace_period — "
        "Docker's 10s default SIGKILLs long jobs on every deploy."
    )
    assert _grace_seconds(grace) >= 60, (
        f"worker stop_grace_period={grace!r} is below the 60s floor; long jobs "
        "need a real drain window before SIGKILL."
    )


def test_worker_drain_window_guard_detects_a_missing_or_short_grace():
    assert _grace_seconds("120s") == 120.0
    assert _grace_seconds("2m") == 120.0
    assert _grace_seconds("1m30s") == 90.0
    assert _grace_seconds("45") == 45.0
    # A service block without the key, or with a short window, must fail the guard.
    assert {"image": "x"}.get("stop_grace_period") is None
    assert _grace_seconds("10s") < 60


def test_prod_overlay_repoints_app_services_at_managed_env():
    """Every app service in the managed overlay must read DATABASE_URL/S3_ENDPOINT
    from .env (not the base's hardcoded @postgres / minio:9000) and drop its
    local-infra depends_on — otherwise managed prod silently talks to a local DB.
    """
    services = _compose_tolerant(_PROD_OVERLAY)["services"]
    for name in _PROD_APP_SERVICES:
        env = services[name]["environment"]
        assert env["DATABASE_URL"].startswith("${DATABASE_URL:?"), name
        assert env["S3_ENDPOINT"].startswith("${S3_ENDPOINT:?"), name
        # depends_on: !reset {} parses to an empty mapping — no local-infra edges.
        assert services[name].get("depends_on") in (None, {}), name


def test_prod_overlay_gates_local_infra_so_a_bare_up_excludes_it():
    """postgres/minio/createbucket must be parked on the never-enabled
    `local-infra` profile, so a bare managed `up` cannot start a stray local
    database or object store alongside the managed cluster.
    """
    services = _compose_tolerant(_PROD_OVERLAY)["services"]
    for name in _PROD_LOCAL_INFRA:
        assert services[name]["profiles"] == ["local-infra"], name


def test_base_compose_passes_verification_email_delivery_to_api_and_webapp():
    """Registration fail-closes 503 without SMTP + a public base URL reaching the
    container (auth_routes.py, auth_web.py). These pass-throughs must live in the
    BASE compose — not a UAT-only overlay — so managed prod (base + prod overlay,
    no UAT) can actually register/verify users instead of silently 503-ing.
    """
    base = _compose("docker-compose.yml")["services"]
    for name in ("api", "webapp"):
        env = base[name]["environment"]
        assert env["DOCRAY_SMTP_HOST"] == "${DOCRAY_SMTP_HOST:-}", name
        assert env["DOCRAY_PUBLIC_BASE_URL"] == "${DOCRAY_PUBLIC_BASE_URL:-}", name
        assert env["DOCRAY_AUTH_MAX_FAILED_LOGINS"] == "${DOCRAY_AUTH_MAX_FAILED_LOGINS:-10}", name


def test_base_compose_passes_stripe_billing_configuration_to_api():
    """The API owns Stripe Checkout/Portal calls and webhook verification."""
    env = _compose("docker-compose.yml")["services"]["api"]["environment"]
    assert env["DOCRAY_STRIPE_SECRET_KEY"] == "${DOCRAY_STRIPE_SECRET_KEY:-}"
    assert env["DOCRAY_STRIPE_WEBHOOK_SECRET"] == "${DOCRAY_STRIPE_WEBHOOK_SECRET:-}"
    assert env["DOCRAY_STRIPE_PRICE_ID"] == "${DOCRAY_STRIPE_PRICE_ID:-}"
    assert env["DOCRAY_STRIPE_SUCCESS_URL"] == "${DOCRAY_STRIPE_SUCCESS_URL:-}"
    assert env["DOCRAY_STRIPE_CANCEL_URL"] == "${DOCRAY_STRIPE_CANCEL_URL:-}"
    assert env["DOCRAY_STRIPE_PORTAL_RETURN_URL"] == "${DOCRAY_STRIPE_PORTAL_RETURN_URL:-}"
    assert env["DOCRAY_STRIPE_API_BASE"] == "${DOCRAY_STRIPE_API_BASE:-https://api.stripe.com/v1}"
    assert (
        env["DOCRAY_BILLING_ENFORCEMENT_ENABLED"]
        == "${DOCRAY_BILLING_ENFORCEMENT_ENABLED:-false}"
    )


def test_nginx_routes_public_stripe_webhook_to_api():
    """Stripe posts to the public origin; nginx must forward that path to the API."""
    text = (_ROOT / "nginx" / "templates" / "default.conf.template").read_text(
        encoding="utf-8"
    )

    assert "location = /stripe/webhook" in text
    stripe_block = text.split("location = /stripe/webhook", 1)[1].split("location", 1)[0]
    assert "proxy_pass http://api:8000" in stripe_block


def test_prod_overlay_binds_the_api_to_loopback_not_the_public_interface():
    """In managed prod the GraphQL API must not publish on 0.0.0.0: it is reached
    intra-network (webapp -> api:8000) or via nginx/TLS, never as raw plaintext
    GraphQL on the public IP. The base binds 0.0.0.0:8000 for local convenience;
    the overlay must override that to loopback.
    """
    api = _compose_tolerant(_PROD_OVERLAY)["services"]["api"]
    assert api["ports"], "prod overlay must override the api ports to loopback"
    for mapping in api["ports"]:
        assert str(mapping).startswith("127.0.0.1:"), mapping


def test_production_uses_a_dedicated_env_file_not_the_dev_env():
    """Production config must be separate from development: the prod make targets
    load .env.prod (gitignored), documented by a committed .env.prod.example.
    Mixing prod secrets/hardening into the dev .env is the bug this guards.
    """
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "--env-file .env.prod" in makefile, "prod targets must load .env.prod, not .env"
    assert (_ROOT / ".env.prod.example").exists(), "commit a .env.prod.example template"
    assert ".env.prod" in (_ROOT / ".gitignore").read_text(encoding="utf-8"), (
        ".env.prod must be gitignored"
    )


def test_prod_env_example_defaults_to_fail_closed_auth():
    """The committed production template must not ship dev's open defaults."""
    text = (_ROOT / ".env.prod.example").read_text(encoding="utf-8")
    assert "DOCRAY_API_ALLOW_ANONYMOUS=false" in text
    assert "DOCRAY_WEBAPP_REQUIRE_AUTH=true" in text
    assert "DOCRAY_API_ALLOW_ANONYMOUS=true" not in text


def test_cloudflared_tunnel_publishes_no_host_ports():
    """The Cloudflare Tunnel ingress must be outbound-only — publishing a host
    port would defeat the point (the droplet's sole public surface is SSH).
    """
    svc = _compose_tolerant("docker-compose.cloudflared.yml")["services"]["cloudflared"]
    assert "ports" not in svc, "cloudflared must not publish host ports (outbound tunnel only)"
    assert svc["restart"] == "unless-stopped"


def test_ollama_resolver_server_is_not_publicly_exposed():
    """The local Ollama model server must bind loopback only — an open inference
    endpoint on the public interface is unauthenticated model access. Also assert
    the overlay routes the workers' resolver to ollama (not a stray backend).
    """
    cfg = _compose_tolerant("docker-compose.ollama.yml")["services"]
    for mapping in cfg["ollama"].get("ports", []):
        assert str(mapping).startswith("127.0.0.1:"), mapping
    for w in ("worker", "worker-fast"):
        assert "ollama" in cfg[w]["environment"]["DOCRAY_RESOLVER_BACKEND"], w


def test_pgadmin_admin_gui_binds_to_loopback_only():
    """pgAdmin exposes an admin surface; it must never publish beyond 127.0.0.1
    (access is via an SSH/VS Code tunnel).
    """
    pgadmin = _compose_tolerant(_PGADMIN_OVERLAY)["services"]["pgadmin"]
    for mapping in pgadmin["ports"]:
        assert str(mapping).startswith("127.0.0.1:"), mapping


def test_webapp_defaults_to_single_gunicorn_worker():
    """Guard the in-memory web auth session store against multi-worker deploys.

    webapp.auth_session.InMemoryAuthSessionStore is process-local: a login
    handled by one gunicorn worker is invisible to the others, so with >1 worker
    a large fraction of authenticated requests fail with "webapp authentication
    required" (and job/upload pages 404). Concurrency must come from --threads,
    not --workers, until a shared (DB-backed) session store lands. This asserts
    the prod overlay's default worker count is 1.
    """
    webapp = _compose_tolerant(_PROD_OVERLAY)["services"]["webapp"]
    entrypoint = webapp["entrypoint"]
    assert "--workers" in entrypoint, "webapp entrypoint must set --workers explicitly"
    workers_value = entrypoint[entrypoint.index("--workers") + 1]
    # Default (the ${VAR:-default} fallback) must be 1 while sessions are in-memory.
    assert workers_value == "${DOCRAY_WEBAPP_WORKERS:-1}", (
        f"webapp worker default must be 1 (in-memory session store); got {workers_value!r}"
    )
