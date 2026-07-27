"""Tests for scripts/prod_check.py.

Every probe takes its I/O collaborator as a keyword argument, so the whole
check suite runs offline: no Postgres, no S3, no docker, no SMTP relay.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error

import prod_check as pc
import pytest

from doc_ray.ports.mailer import MailerError

FULL_ENV = {
    "DATABASE_URL": "postgresql://u:p@managed.example.com:25060/docray",
    "S3_ENDPOINT": "https://tor1.digitaloceanspaces.com",
    "S3_BUCKET": "docray",
    "S3_ACCESS_KEY": "key",
    "S3_SECRET_KEY": "secret",
    "DOCRAY_API_JWT_SECRET": "jwt",
    "DOCRAY_WEBAPP_SECRET_KEY": "web",
    "DOCRAY_PUBLIC_BASE_URL": "https://doc-ray.com",
    "DOCRAY_SMTP_HOST": "mail.smtp2go.com",
    "DOCRAY_SMTP_PORT": "587",
    "DOCRAY_SMTP_FROM": "noreply@doc-ray.com",
    "DOCRAY_SMTP_USERNAME": "doc-ray.com",
    "DOCRAY_SMTP_PASSWORD": "relay-password",
    "CLOUDFLARE_TUNNEL_TOKEN": "token",
    "DOCRAY_API_ALLOW_ANONYMOUS": "false",
    "DOCRAY_API_DEBUG": "false",
    "DOCRAY_WEBAPP_SESSION_COOKIE_SECURE": "true",
}


# --- env file loading -------------------------------------------------------


def test_load_env_file_parses_exports_comments_and_quotes(tmp_path) -> None:
    path = tmp_path / ".env.prod"
    path.write_text(
        "# comment\n"
        "\n"
        "export DATABASE_URL='postgresql://u:p@host/db'\n"
        'DOCRAY_SMTP_FROM="noreply@doc-ray.com"\n'
        "DOCRAY_API_TOKENS=a=b,c=d\n"
        "malformed-line\n",
        encoding="utf-8",
    )

    env = pc.load_env_file(path)

    assert env["DATABASE_URL"] == "postgresql://u:p@host/db"
    assert env["DOCRAY_SMTP_FROM"] == "noreply@doc-ray.com"
    # Values may legitimately contain '=' — only the first one splits.
    assert env["DOCRAY_API_TOKENS"] == "a=b,c=d"
    assert "malformed-line" not in env


# --- configuration ----------------------------------------------------------


def test_check_env_fails_and_names_every_missing_key() -> None:
    env = dict(FULL_ENV)
    del env["DATABASE_URL"]
    env["DOCRAY_SMTP_PASSWORD"] = "   "

    result = pc.check_env(env)

    assert result.status == pc.FAIL
    assert "DATABASE_URL" in result.detail
    assert "DOCRAY_SMTP_PASSWORD" in result.detail


def test_check_env_passes_on_a_complete_production_env() -> None:
    assert pc.check_env(FULL_ENV).status == pc.OK


def test_check_posture_warns_on_anonymous_api_and_debug() -> None:
    env = {**FULL_ENV, "DOCRAY_API_ALLOW_ANONYMOUS": "true", "DOCRAY_API_DEBUG": "true"}

    result = pc.check_posture(env)

    assert result.status == pc.WARN
    assert "DOCRAY_API_ALLOW_ANONYMOUS" in result.detail
    assert "DOCRAY_API_DEBUG" in result.detail


def test_check_posture_passes_on_hardened_settings() -> None:
    assert pc.check_posture(FULL_ENV).status == pc.OK


def test_check_posture_accepts_a_blank_key_whose_runtime_default_is_safe() -> None:
    """DOCRAY_API_DEBUG defaults to off, so leaving it blank is not a finding."""
    assert pc.check_posture({**FULL_ENV, "DOCRAY_API_DEBUG": ""}).status == pc.OK


def test_check_posture_warns_when_an_unset_key_defaults_to_the_unsafe_value() -> None:
    """DOCRAY_API_ALLOW_ANONYMOUS defaults to true — unset means an open API."""
    env = {k: v for k, v in FULL_ENV.items() if k != "DOCRAY_API_ALLOW_ANONYMOUS"}

    result = pc.check_posture(env)

    assert result.status == pc.WARN
    assert "runtime default" in result.detail


# --- database ---------------------------------------------------------------


class _FakeConn:
    def __init__(self, version: str = "1.33.0") -> None:
        self._version = version

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, *args: object) -> _FakeConn:
        return self

    def fetchone(self) -> tuple[str]:
        return (self._version,)


def test_check_database_reports_version_and_pending_migrations(monkeypatch) -> None:
    monkeypatch.setattr(pc.migrate_mod, "pending_migrations", lambda conn: [])

    result = pc.check_database(FULL_ENV, connect=lambda url, **kw: _FakeConn())

    assert result.status == pc.OK
    assert "1.33.0" in result.detail


def test_check_database_fails_when_migrations_are_pending(monkeypatch) -> None:
    monkeypatch.setattr(
        pc.migrate_mod, "pending_migrations", lambda conn: ["102_x.sql", "103_y.sql"]
    )

    result = pc.check_database(FULL_ENV, connect=lambda url, **kw: _FakeConn())

    assert result.status == pc.FAIL
    assert "2 pending" in result.detail


def test_check_database_fails_when_the_cluster_is_unreachable() -> None:
    def _boom(url: str, **kw: object) -> _FakeConn:
        raise OSError("connection refused")

    result = pc.check_database(FULL_ENV, connect=_boom)

    assert result.status == pc.FAIL
    assert "connection refused" in result.detail


class _RollupConn:
    def __init__(self, failing_tenants: int) -> None:
        self._failing_tenants = failing_tenants
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, *args: object):
        self.executed.append(sql)
        return self

    def fetchone(self) -> tuple[int]:
        return (self._failing_tenants,)


def test_check_corpus_rollups_fails_without_leaking_tenant_ids() -> None:
    result = pc.check_corpus_rollups(FULL_ENV, connect=lambda url, **kw: _RollupConn(2))

    assert result.status == pc.FAIL
    assert result.name == "corpus_rollups"
    assert "2" in result.detail
    assert "tenant" in result.detail


def test_check_corpus_rollups_passes_when_rollups_match() -> None:
    conn = _RollupConn(0)
    result = pc.check_corpus_rollups(FULL_ENV, connect=lambda url, **kw: conn)

    assert result.status == pc.OK
    assert conn.executed[0] == "SET ROLE docray_batch"


def test_corpus_rollup_probe_never_aggregates_fact_tables_live() -> None:
    sql = pc._CORPUS_ROLLUP_HEALTH_SQL.lower()

    assert "document_stats" in sql
    assert "corpus_stats" in sql
    assert "lexeme_stats" in sql
    assert "from sentences" not in sql
    assert "lexeme_occurrences" not in sql


def test_run_checks_includes_the_corpus_rollup_probe(monkeypatch) -> None:
    """The probe is only protective if the production command actually runs it."""

    def result(name: str):
        return pc.CheckResult(name, pc.OK, "ok")

    for name in (
        "check_env",
        "check_posture",
        "check_database",
        "check_object_store",
        "check_api",
        "check_webapp",
        "check_mcp",
        "check_tunnel",
        "check_public",
        "check_mcp_public",
        "check_smtp",
    ):
        monkeypatch.setattr(pc, name, lambda *args, _name=name, **kwargs: result(_name))
    monkeypatch.setattr(
        pc,
        "check_corpus_rollups",
        lambda *args, **kwargs: result("corpus_rollups"),
        raising=False,
    )

    names = [r.name for r in pc.run_checks(FULL_ENV, recipient=None, send=False)]

    assert "corpus_rollups" in names


# --- object store -----------------------------------------------------------


def test_check_object_store_pings_the_configured_bucket() -> None:
    pinged: list[str] = []

    class _Store:
        def ping(self) -> None:
            pinged.append("ping")

    result = pc.check_object_store(FULL_ENV, store_factory=lambda cfg: _Store())

    assert result.status == pc.OK
    assert pinged == ["ping"]
    assert "docray" in result.detail


def test_check_object_store_fails_on_rejected_credentials() -> None:
    class _Store:
        def ping(self) -> None:
            raise RuntimeError("403 Forbidden")

    result = pc.check_object_store(FULL_ENV, store_factory=lambda cfg: _Store())

    assert result.status == pc.FAIL
    assert "403" in result.detail


# --- HTTP surfaces ----------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, body: bytes = b"{}", headers: dict | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body


def test_check_api_ready_reads_the_readiness_report() -> None:
    body = json.dumps({"status": "ready", "ready": True, "checks": {"database": "ok"}}).encode()

    result = pc.check_api(
        "http://127.0.0.1:8000", opener=lambda req, timeout: _FakeResponse(200, body)
    )

    assert result.status == pc.OK
    assert "ready" in result.detail


def test_check_api_fails_when_a_dependency_is_not_ready() -> None:
    body = json.dumps(
        {"status": "not_ready", "ready": False, "checks": {"object_store": "unreachable"}}
    ).encode()

    result = pc.check_api(
        "http://127.0.0.1:8000", opener=lambda req, timeout: _FakeResponse(503, body)
    )

    assert result.status == pc.FAIL
    assert "object_store" in result.detail


def test_check_public_confirms_the_response_came_through_cloudflare() -> None:
    response = _FakeResponse(200, b"<html>", {"cf-ray": "8abc-GRU", "server": "cloudflare"})

    result = pc.check_public("https://doc-ray.com", opener=lambda req, timeout: response)

    assert result.status == pc.OK
    assert "cf-ray" in result.detail.lower()


def test_check_public_warns_when_no_cloudflare_edge_header_is_present() -> None:
    """A 200 without cf-ray means the probe bypassed the tunnel (e.g. local DNS)."""
    result = pc.check_public(
        "https://doc-ray.com", opener=lambda req, timeout: _FakeResponse(200, b"<html>", {})
    )

    assert result.status == pc.WARN


def test_check_public_diagnoses_cloudflare_error_1033(monkeypatch) -> None:
    def _raise(req: object, timeout: float) -> _FakeResponse:
        raise pc.urllib.error.HTTPError(
            "https://doc-ray.com", 530, "Origin DNS error", {"cf-ray": "8abc"}, None
        )

    result = pc.check_public("https://doc-ray.com", opener=_raise)

    assert result.status == pc.FAIL
    assert "1033" in result.detail


def test_check_mcp_accepts_an_authenticated_wall() -> None:
    """401 is the healthy answer: the server is up and refuses anonymous callers."""
    result = pc.check_mcp("http://127.0.0.1:8080", opener=lambda req, timeout: _FakeResponse(401))

    assert result.status == pc.OK
    assert "401" in result.detail


def test_check_mcp_fails_when_the_container_is_not_running() -> None:
    def _refused(req: object, timeout: float) -> _FakeResponse:
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    result = pc.check_mcp("http://127.0.0.1:8080", opener=_refused)

    assert result.status == pc.FAIL
    assert "make mcp-up" in result.detail


def test_check_mcp_fails_when_the_surface_answers_anonymously() -> None:
    """200 without a token means DOCRAY_MCP_ALLOW_ANONYMOUS leaked into production."""
    result = pc.check_mcp("http://127.0.0.1:8080", opener=lambda req, timeout: _FakeResponse(200))

    assert result.status == pc.FAIL
    assert "anonymous" in result.detail.lower()


def test_check_mcp_public_passes_on_an_auth_challenge_through_cloudflare() -> None:
    response = _FakeResponse(401, b"", {"cf-ray": "8abc"})

    result = pc.check_mcp_public("https://doc-ray.com", opener=lambda req, timeout: response)

    assert result.status == pc.OK


def test_check_mcp_public_diagnoses_a_route_shadowed_by_the_webapp_catch_all() -> None:
    def _redirect(req: object, timeout: float) -> _FakeResponse:
        raise urllib.error.HTTPError(
            "https://doc-ray.com/mcp", 302, "Found", {"location": "/login"}, None
        )

    result = pc.check_mcp_public("https://doc-ray.com", opener=_redirect)

    assert result.status == pc.FAIL
    assert "/login" in result.detail
    assert "catch-all" in result.detail


def test_check_mcp_public_diagnoses_the_dns_rebind_host_guard() -> None:
    def _rebind(req: object, timeout: float) -> _FakeResponse:
        raise urllib.error.HTTPError(
            "https://doc-ray.com/mcp", 421, "Misdirected Request", {}, None
        )

    result = pc.check_mcp_public("https://doc-ray.com", opener=_rebind)

    assert result.status == pc.FAIL
    assert "DOCRAY_MCP_ALLOWED_HOSTS" in result.detail


# --- Cloudflare tunnel ------------------------------------------------------


def _runner(results: dict[str, subprocess.CompletedProcess]):
    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        key = "logs" if "logs" in cmd else "inspect"
        return results[key]

    return run


def test_check_tunnel_reports_registered_edge_connections() -> None:
    runner = _runner(
        {
            "inspect": subprocess.CompletedProcess([], 0, "running 0\n", ""),
            "logs": subprocess.CompletedProcess(
                [],
                0,
                "",
                "INF Registered tunnel connection connIndex=0\n"
                "INF Registered tunnel connection connIndex=1\n",
            ),
        }
    )

    result = pc.check_tunnel(runner=runner)

    assert result.status == pc.OK
    assert "2" in result.detail


def test_check_tunnel_fails_when_the_container_is_absent() -> None:
    runner = _runner(
        {
            "inspect": subprocess.CompletedProcess([], 1, "", "No such object: docray-cloudflared"),
            "logs": subprocess.CompletedProcess([], 1, "", ""),
        }
    )

    result = pc.check_tunnel(runner=runner)

    assert result.status == pc.FAIL
    assert "make prod-tunnel" in result.detail


def test_check_tunnel_fails_when_no_connection_is_registered() -> None:
    runner = _runner(
        {
            "inspect": subprocess.CompletedProcess([], 0, "running 4\n", ""),
            "logs": subprocess.CompletedProcess(
                [], 0, "", "ERR Failed to serve tunnel connection\n"
            ),
        }
    )

    result = pc.check_tunnel(runner=runner)

    assert result.status == pc.FAIL
    assert "no edge connection" in result.detail.lower()


def test_check_tunnel_warns_on_origin_errors_behind_a_live_tunnel() -> None:
    runner = _runner(
        {
            "inspect": subprocess.CompletedProcess([], 0, "running 0\n", ""),
            "logs": subprocess.CompletedProcess(
                [],
                0,
                "",
                "INF Registered tunnel connection connIndex=0\n"
                "ERR Unable to reach the origin service error=dial tcp: connect: "
                "connection refused\n",
            ),
        }
    )

    result = pc.check_tunnel(runner=runner)

    assert result.status == pc.WARN
    assert "origin" in result.detail.lower()


def test_check_tunnel_ignores_origin_errors_older_than_the_window() -> None:
    """Errors logged while a service was restarting must not warn forever."""
    runner = _runner(
        {
            "inspect": subprocess.CompletedProcess([], 0, "running 0\n", ""),
            "logs": subprocess.CompletedProcess(
                [],
                0,
                "",
                "2020-01-01T00:00:00Z INF Registered tunnel connection connIndex=0\n"
                "2020-01-01T00:05:00Z ERR Unable to reach the origin service\n",
            ),
        }
    )

    result = pc.check_tunnel(runner=runner)

    assert result.status == pc.OK


def test_check_tunnel_skips_cleanly_when_docker_is_unavailable() -> None:
    def _no_docker(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise FileNotFoundError("docker")

    assert pc.check_tunnel(runner=_no_docker).status == pc.SKIP


# --- SMTP -------------------------------------------------------------------


class _FakeMailer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[dict] = []
        self.verified = 0

    def describe(self) -> str:
        return "mail.smtp2go.com:587 starttls user=doc-ray.com from=noreply@doc-ray.com"

    def send(self, *, to: str, subject: str, body: str) -> None:
        if self.error:
            raise self.error
        self.sent.append({"to": to, "subject": subject, "body": body})

    def verify(self) -> None:
        if self.error:
            raise self.error
        self.verified += 1


def test_check_smtp_sends_a_real_message_to_the_recipient() -> None:
    mailer = _FakeMailer()

    result = pc.check_smtp(
        FULL_ENV, recipient="ops@example.com", send=True, mailer_factory=lambda env: mailer
    )

    assert result.status == pc.OK
    assert mailer.sent[0]["to"] == "ops@example.com"
    assert "doc-ray" in mailer.sent[0]["subject"]
    assert "mail.smtp2go.com:587" in result.detail
    assert "relay-password" not in result.detail


def test_check_smtp_reports_relay_rejection_as_a_failure() -> None:
    mailer = _FakeMailer(MailerError("verification email delivery failed: 535 auth"))

    result = pc.check_smtp(
        FULL_ENV, recipient="ops@example.com", send=True, mailer_factory=lambda env: mailer
    )

    assert result.status == pc.FAIL
    assert "535" in result.detail


def test_check_smtp_verifies_without_sending_when_send_is_disabled() -> None:
    mailer = _FakeMailer()

    result = pc.check_smtp(
        FULL_ENV, recipient="ops@example.com", send=False, mailer_factory=lambda env: mailer
    )

    assert result.status == pc.OK
    assert mailer.sent == []
    assert mailer.verified == 1


def test_check_smtp_fails_when_smtp_is_not_configured_at_all() -> None:
    result = pc.check_smtp({}, recipient=None, send=True, mailer_factory=lambda env: None)

    assert result.status == pc.FAIL
    assert "DOCRAY_SMTP_HOST" in result.detail


def test_check_smtp_falls_back_to_the_envelope_sender_as_recipient() -> None:
    mailer = _FakeMailer()

    pc.check_smtp(FULL_ENV, recipient=None, send=True, mailer_factory=lambda env: mailer)

    assert mailer.sent[0]["to"] == "noreply@doc-ray.com"


def test_resolve_recipient_prefers_the_explicit_argument() -> None:
    env = {**FULL_ENV, "DOCRAY_SMTP_CHECK_TO": "checks@doc-ray.com"}
    assert pc.resolve_recipient(env, "cli@example.com") == "cli@example.com"
    assert pc.resolve_recipient(env, None) == "checks@doc-ray.com"
    assert pc.resolve_recipient(FULL_ENV, None) == "noreply@doc-ray.com"


# --- aggregation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([pc.OK, pc.OK], 0),
        ([pc.OK, pc.SKIP], 0),
        ([pc.OK, pc.WARN], 1),
        ([pc.WARN, pc.FAIL], 2),
    ],
)
def test_exit_code_escalates_with_the_worst_status(statuses: list[str], expected: int) -> None:
    results = [pc.CheckResult(f"c{i}", status, "") for i, status in enumerate(statuses)]
    assert pc.exit_code(results) == expected


def test_render_json_is_machine_readable() -> None:
    results = [pc.CheckResult("smtp", pc.OK, "delivered to ops@example.com")]

    payload = json.loads(pc.render_json(results))

    assert payload["status"] == pc.OK
    assert payload["checks"][0]["name"] == "smtp"
