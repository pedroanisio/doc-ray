#!/usr/bin/env python3
"""Verify a production deployment end to end, including outbound mail delivery.

Answers one question — *is production actually working right now?* — by probing
the real dependencies with the same adapters the running services use:

    config        every key production needs is present in the env file
    posture       anonymous API access, debug mode, and cookie flags are hardened
    database      the managed cluster answers, and no migration is pending
    corpus_rollups tenant rollups agree with their maintained document/lexeme inputs
    object store  the Spaces/MinIO bucket answers a credentialed head request
    api           the local API's ``/readyz`` reports every dependency ready
    webapp        the local webapp answers on its loopback port
    mcp           the loopback MCP server serves and enforces authentication
    tunnel        the Cloudflare Tunnel container has live edge connections
    public        the public URL answers *through* the Cloudflare edge
    mcp_public    ``/mcp`` reaches the MCP server, not the webapp login page
    smtp          a real message is delivered to the relay (SMTP2GO)

The env file is authoritative: values in ``.env.prod`` override the ambient
shell, mirroring ``docker compose --env-file .env.prod``, so a development
``DATABASE_URL`` in the operator's shell can never silently retarget the check.

Read-only apart from the SMTP probe, which by design hands one real message to
the relay. Use ``--no-send`` to verify reachability, TLS, and credentials
without delivering anything.

Usage::

    python scripts/prod_check.py
    python scripts/prod_check.py --to ops@example.com
    python scripts/prod_check.py --no-send --json
    python scripts/prod_check.py --env-file .env.uat

Exit codes: 0 = all green, 1 = warnings only, 2 = at least one failed check,
3 = the env file is missing or unreadable.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from _prod_check_database import CheckResult, check_corpus_rollups, check_database
from _prod_check_queries import CORPUS_ROLLUP_HEALTH_SQL as _CORPUS_ROLLUP_HEALTH_SQL

from doc_ray.adapters import migrate as migrate_mod
from doc_ray.adapters.mailer_smtp import SmtpMailer
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config
from doc_ray.ports.mailer import MailerError

__all__ = ["_CORPUS_ROLLUP_HEALTH_SQL", "migrate_mod"]

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

#: Worst-status-wins ordering used for the aggregate exit code.
_SEVERITY = {SKIP: 0, OK: 0, WARN: 1, FAIL: 2}

#: Keys with no safe default: production is misconfigured without them.
REQUIRED_KEYS = (
    "DATABASE_URL",
    "S3_ENDPOINT",
    "S3_BUCKET",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "DOCRAY_API_JWT_SECRET",
    "DOCRAY_WEBAPP_SECRET_KEY",
    "DOCRAY_PUBLIC_BASE_URL",
    "DOCRAY_SMTP_HOST",
    "DOCRAY_SMTP_USERNAME",
    "DOCRAY_SMTP_PASSWORD",
    "DOCRAY_SMTP_FROM",
    "CLOUDFLARE_TUNNEL_TOKEN",
)

#: Security-relevant settings, mapped to (value production must carry, value the
#: code applies when the key is unset). Blank is only a finding when the runtime
#: default is itself unsafe — DOCRAY_API_ALLOW_ANONYMOUS defaults to *true*, so an
#: unset key there opens the API, while an unset DOCRAY_API_DEBUG stays off.
POSTURE_KEYS = {
    "DOCRAY_API_ALLOW_ANONYMOUS": ("false", "true"),
    "DOCRAY_API_DEBUG": ("false", "false"),
    "DOCRAY_WEBAPP_SESSION_COOKIE_SECURE": ("true", "true"),
}

#: Container name fixed by docker-compose.cloudflared.yml.
TUNNEL_CONTAINER = "docray-cloudflared"

#: cloudflared log markers: one registration line per live edge connection.
_TUNNEL_REGISTERED = "Registered tunnel connection"
_TUNNEL_ORIGIN_ERRORS = ("Unable to reach the origin", "connection refused", "no such host")

#: How far back a cloudflared origin error still counts as current. Errors older
#: than this are the trailing edge of a completed restart, not a live fault.
ORIGIN_ERROR_WINDOW_SECONDS = 900

HTTP_TIMEOUT_SECONDS = 10.0


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=value`` env file, tolerating comments, blanks, and ``export``.

    Args:
        path: The env file to read (typically ``.env.prod``).

    Returns:
        The parsed mapping; malformed lines are skipped rather than fatal.
    """
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").lstrip().partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


# --- configuration ----------------------------------------------------------


def check_env(env: Mapping[str, str]) -> CheckResult:
    """Report every required production key that is absent or blank."""
    missing = [key for key in REQUIRED_KEYS if not (env.get(key) or "").strip()]
    if missing:
        return CheckResult("config", FAIL, f"missing/blank: {', '.join(missing)}")
    return CheckResult("config", OK, f"all {len(REQUIRED_KEYS)} required keys present")


def check_posture(env: Mapping[str, str]) -> CheckResult:
    """Flag development-friendly settings that must not survive into production.

    Compares the *effective* value — the configured one, or the default the code
    falls back to when the key is blank — so an unset key is reported only when
    its fallback is the unsafe one.
    """
    wrong = []
    for key, (want, fallback) in POSTURE_KEYS.items():
        raw = (env.get(key) or "").strip().lower()
        effective = raw or fallback
        if effective != want:
            source = raw or f"{fallback} (unset — runtime default)"
            wrong.append(f"{key}={source} (want {want})")
    if not (env.get("DOCRAY_PUBLIC_BASE_URL") or "").startswith("https://"):
        wrong.append("DOCRAY_PUBLIC_BASE_URL is not https")
    if wrong:
        return CheckResult("posture", WARN, "; ".join(wrong))
    return CheckResult("posture", OK, "anonymous API off, debug off, secure cookies, https base")


# --- stateful dependencies --------------------------------------------------


def check_object_store(
    env: Mapping[str, str], *, store_factory: Callable[[Config], Any] = ObjectStore
) -> CheckResult:
    """Head the configured bucket with the production credentials."""
    bucket = env.get("S3_BUCKET", "")
    cfg = Config(
        database_url=env.get("DATABASE_URL", ""),
        s3_endpoint=env.get("S3_ENDPOINT", ""),
        s3_access_key=env.get("S3_ACCESS_KEY", ""),
        s3_secret_key=env.get("S3_SECRET_KEY", ""),
        s3_bucket=bucket,
        s3_region=env.get("S3_REGION", "us-east-1"),
        s3_allow_insecure_endpoint=True,  # the posture check owns endpoint hygiene
    )
    try:
        store_factory(cfg).ping()
    except Exception as exc:
        return CheckResult("object_store", FAIL, f"{type(exc).__name__}: {exc}")
    return CheckResult("object_store", OK, f"bucket {bucket!r} reachable at {cfg.s3_endpoint}")


# --- HTTP surfaces ----------------------------------------------------------

Opener = Callable[..., Any]


def _http_get(url: str, opener: Opener) -> tuple[int, bytes, Mapping[str, str]]:
    """GET *url*, normalizing an HTTP error response into a plain status/body tuple."""
    request = urllib.request.Request(url, headers={"User-Agent": "doc-ray-prod-check"})
    try:
        with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return int(response.status), response.read(4096), dict(response.headers or {})
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read() if hasattr(exc, "read") else b"", dict(exc.headers or {})


def check_api(base_url: str, *, opener: Opener = urllib.request.urlopen) -> CheckResult:
    """Read the API's ``/readyz`` report — the per-dependency readiness contract."""
    url = f"{base_url.rstrip('/')}/readyz"
    try:
        status, body, _ = _http_get(url, opener)
    except Exception as exc:
        return CheckResult("api", FAIL, f"{url}: {type(exc).__name__}: {exc}")
    try:
        report = json.loads(body or b"{}")
    except ValueError:
        return CheckResult("api", FAIL, f"{url}: HTTP {status}, unparseable body")
    checks = report.get("checks", {})
    if status == 200 and report.get("ready"):
        return CheckResult("api", OK, f"{url}: ready ({', '.join(sorted(checks))})")
    broken = ", ".join(f"{k}={v}" for k, v in checks.items() if v != "ok") or "no detail"
    return CheckResult("api", FAIL, f"{url}: HTTP {status} not ready — {broken}")


def check_webapp(base_url: str, *, opener: Opener = urllib.request.urlopen) -> CheckResult:
    """Confirm the loopback webapp answers; its index queries Postgres."""
    try:
        status, _, _ = _http_get(base_url, opener)
    except Exception as exc:
        return CheckResult("webapp", FAIL, f"{base_url}: {type(exc).__name__}: {exc}")
    if status >= 500:
        return CheckResult("webapp", FAIL, f"{base_url}: HTTP {status}")
    return CheckResult("webapp", OK, f"{base_url}: HTTP {status}")


def check_public(base_url: str, *, opener: Opener = urllib.request.urlopen) -> CheckResult:
    """Fetch the public URL and confirm it was served through the Cloudflare edge.

    Cloudflare answers HTTP 530 when the tunnel cannot serve the hostname
    (Error 1033) — the exact symptom of a dropped cloudflared container, so it
    is diagnosed by name instead of being reported as a generic 5xx.
    """
    try:
        status, _, headers = _http_get(base_url, opener)
    except Exception as exc:
        return CheckResult("public", FAIL, f"{base_url}: {type(exc).__name__}: {exc}")
    lower = {k.lower(): v for k, v in headers.items()}
    ray = lower.get("cf-ray")
    if status == 530:
        return CheckResult(
            "public",
            FAIL,
            f"{base_url}: HTTP 530 — Cloudflare Error 1033: no tunnel is serving this "
            "hostname (run `make prod-tunnel` and check the public-hostname route)",
        )
    if status >= 500:
        return CheckResult("public", FAIL, f"{base_url}: HTTP {status} (cf-ray={ray or 'none'})")
    if not ray:
        return CheckResult(
            "public",
            WARN,
            f"{base_url}: HTTP {status} but no cf-ray header — the response did not come "
            "through Cloudflare (local DNS override or direct origin hit)",
        )
    return CheckResult("public", OK, f"{base_url}: HTTP {status} via Cloudflare (cf-ray={ray})")


def _no_redirect_opener() -> Opener:
    """Return an opener that surfaces 3xx as an HTTPError instead of following it.

    A redirect must stay visible for the MCP route check: urllib would silently
    follow ``/mcp -> /login`` to a 200 login page, hiding the exact
    misconfiguration the probe exists to catch.
    """

    class _KeepRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    return urllib.request.build_opener(_KeepRedirect).open


def _mcp_verdict(name: str, url: str, status: int, extra: str = "") -> CheckResult:
    """Grade an MCP probe: an auth challenge is healthy, an anonymous 200 is not.

    The MCP surface has no unauthenticated route by design (the compose
    healthcheck can only test the TCP port), so 401/403 is the *expected*
    success signal — it proves uvicorn is serving and the auth layer is on.
    """
    if status in (401, 403):
        return CheckResult(name, OK, f"{url}: HTTP {status} — serving, auth enforced{extra}")
    if status == 200:
        return CheckResult(
            name,
            FAIL,
            f"{url}: HTTP 200 without a token — the MCP surface is answering "
            "anonymously (DOCRAY_MCP_ALLOW_ANONYMOUS must be false in production)",
        )
    if status >= 500:
        return CheckResult(name, FAIL, f"{url}: HTTP {status}")
    return CheckResult(name, WARN, f"{url}: HTTP {status} — expected a 401 auth challenge")


def check_mcp(local_base: str, *, opener: Opener = urllib.request.urlopen) -> CheckResult:
    """Probe the loopback MCP server that fronts the GraphQL API for agents."""
    url = f"{local_base.rstrip('/')}/mcp"
    try:
        status, _, _ = _http_get(url, opener)
    except Exception as exc:
        return CheckResult(
            "mcp",
            FAIL,
            f"{url}: {type(exc).__name__}: {exc} — run `make mcp-up` (the overlay alone "
            "cannot resolve its `api` dependency; it must layer on the prod stack)",
        )
    return _mcp_verdict("mcp", url, status)


def check_mcp_public(public_base: str, *, opener: Opener | None = None) -> CheckResult:
    """Confirm the public ``/mcp`` path reaches the MCP server, not the webapp.

    Two documented production failure modes are diagnosed by name: a Cloudflare
    route ordered below the webapp catch-all (``/mcp`` 302s to ``/login``), and
    the MCP SDK's DNS-rebind guard rejecting the public Host with 421.
    """
    url = f"{public_base.rstrip('/')}/mcp"
    try:
        status, _, headers = _http_get(url, opener or _no_redirect_opener())
    except Exception as exc:
        return CheckResult("mcp_public", FAIL, f"{url}: {type(exc).__name__}: {exc}")
    lower = {k.lower(): v for k, v in headers.items()}
    if status in (301, 302, 303, 307, 308):
        return CheckResult(
            "mcp_public",
            FAIL,
            f"{url}: HTTP {status} -> {lower.get('location', '?')} — the Cloudflare route "
            "`/mcp -> http://mcp:8080` is shadowed by the webapp catch-all; move it above",
        )
    if status == 421:
        return CheckResult(
            "mcp_public",
            FAIL,
            f"{url}: HTTP 421 Invalid Host header — the MCP SDK's DNS-rebind guard rejects "
            "the public host; set DOCRAY_PUBLIC_BASE_URL/DOCRAY_MCP_ALLOWED_HOSTS on `mcp`",
        )
    ray = lower.get("cf-ray")
    return _mcp_verdict("mcp_public", url, status, f" (cf-ray={ray})" if ray else "")


# --- Cloudflare tunnel ------------------------------------------------------

Runner = Callable[..., subprocess.CompletedProcess]


def _docker(runner: Runner, args: list[str]) -> subprocess.CompletedProcess:
    return runner(["docker", *args], capture_output=True, text=True, timeout=20)


def _is_recent(line: str, cutoff: str) -> bool:
    """True when a cloudflared log line is newer than *cutoff*, or carries no timestamp.

    cloudflared prefixes each line with an RFC 3339 UTC stamp, which compares
    correctly as a string. Lines without one cannot be aged out, so they are
    kept rather than silently dropped.
    """
    head = line.split(" ", 1)[0]
    stamped = len(head) == 20 and head.endswith("Z") and head[4] == "-"
    return not stamped or head >= cutoff


def check_tunnel(*, runner: Runner = subprocess.run) -> CheckResult:
    """Inspect the cloudflared container and count its registered edge connections.

    The public probe proves the path works end to end; this one localizes a
    break to the origin side — container gone, restart-looping, connected to the
    edge but unable to reach ``webapp:5001``.
    """
    try:
        inspect = _docker(
            runner, ["inspect", "-f", "{{.State.Status}} {{.RestartCount}}", TUNNEL_CONTAINER]
        )
    except FileNotFoundError:
        return CheckResult("tunnel", SKIP, "docker is not available on this host")
    except Exception as exc:
        return CheckResult("tunnel", SKIP, f"docker inspect failed: {type(exc).__name__}: {exc}")
    if inspect.returncode != 0:
        return CheckResult(
            "tunnel",
            FAIL,
            f"container {TUNNEL_CONTAINER} not found — run `make prod-tunnel` "
            "(the public URL will serve Cloudflare Error 1033 until it is up)",
        )
    parts = (inspect.stdout or "").split()
    state = parts[0] if parts else "unknown"
    restarts = parts[1] if len(parts) > 1 else "0"
    if state != "running":
        return CheckResult("tunnel", FAIL, f"container {TUNNEL_CONTAINER} is {state}")

    logs = _docker(runner, ["logs", "--tail", "400", TUNNEL_CONTAINER])
    text = (logs.stdout or "") + (logs.stderr or "")
    registered = text.count(_TUNNEL_REGISTERED)
    if registered == 0:
        return CheckResult(
            "tunnel",
            FAIL,
            f"container is running but no edge connection is registered in the last 400 "
            f"log lines (restarts={restarts})",
        )
    # Only *recent* origin errors are a finding: cloudflared keeps the lines it
    # logged while a downstream service was being restarted, and a warning that
    # never clears after a deploy is a warning operators learn to ignore.
    cutoff = (
        f"{datetime.now(UTC) - timedelta(seconds=ORIGIN_ERROR_WINDOW_SECONDS):%Y-%m-%dT%H:%M:%SZ}"
    )
    origin_errors = [
        line.strip()
        for line in text.splitlines()
        if any(marker in line for marker in _TUNNEL_ORIGIN_ERRORS) and _is_recent(line, cutoff)
    ]
    if origin_errors:
        return CheckResult(
            "tunnel",
            WARN,
            f"{registered} edge connections, but cloudflared logged origin errors: "
            f"{origin_errors[-1][:160]}",
        )
    if restarts != "0":
        return CheckResult(
            "tunnel", WARN, f"{registered} edge connections registered, but restarts={restarts}"
        )
    return CheckResult("tunnel", OK, f"{registered} edge connections registered, restarts=0")


# --- SMTP -------------------------------------------------------------------


def resolve_recipient(env: Mapping[str, str], explicit: str | None) -> str | None:
    """Pick the delivery-test recipient: CLI argument, DOCRAY_SMTP_CHECK_TO, then sender."""
    for candidate in (explicit, env.get("DOCRAY_SMTP_CHECK_TO"), env.get("DOCRAY_SMTP_FROM")):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _probe_message(env: Mapping[str, str]) -> tuple[str, str]:
    """Build the (subject, body) of the delivery-test message."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    subject = f"doc-ray production check — SMTP delivery test {stamp}"
    body = (
        "This is an automated delivery test from the doc-ray production check.\n\n"
        f"    relay:  {env.get('DOCRAY_SMTP_HOST', '')}:{env.get('DOCRAY_SMTP_PORT', '587')}\n"
        f"    sender: {env.get('DOCRAY_SMTP_FROM', '')}\n"
        f"    site:   {env.get('DOCRAY_PUBLIC_BASE_URL', '')}\n"
        f"    sent:   {stamp}\n\n"
        "Receiving it confirms that account registration and email verification\n"
        "can deliver mail. No action is required.\n"
    )
    return subject, body


def check_smtp(
    env: Mapping[str, str],
    *,
    recipient: str | None,
    send: bool = True,
    mailer_factory: Callable[[Mapping[str, str]], Any] = SmtpMailer.from_env,
) -> CheckResult:
    """Deliver one real message through the configured relay (SMTP2GO in production).

    Registration answers 503 when the relay is misconfigured, and neither the
    API nor the webapp probes touch outbound mail — so the only honest check is
    to hand the relay a message and see it accepted. ``send=False`` degrades to
    a connect/TLS/AUTH verification that delivers nothing.
    """
    mailer = mailer_factory(env)
    if mailer is None:
        return CheckResult("smtp", FAIL, "no mailer: DOCRAY_SMTP_HOST is unset")
    transport = mailer.describe()
    if not send:
        try:
            mailer.verify()
        except MailerError as exc:
            return CheckResult("smtp", FAIL, f"{transport} — {exc}")
        return CheckResult("smtp", OK, f"{transport} — connect/TLS/AUTH ok (nothing sent)")

    target = resolve_recipient(env, recipient)
    if not target:
        return CheckResult("smtp", WARN, f"{transport} — no recipient; pass --to to test delivery")
    subject, body = _probe_message(env)
    try:
        mailer.send(to=target, subject=subject, body=body)
    except MailerError as exc:
        return CheckResult("smtp", FAIL, f"{transport} — {exc}")
    return CheckResult("smtp", OK, f"{transport} — accepted for delivery to {target}")


# --- orchestration ----------------------------------------------------------


def run_checks(env: Mapping[str, str], *, recipient: str | None, send: bool) -> list[CheckResult]:
    """Run every probe in dependency order and collect the verdicts."""
    api_url = f"http://127.0.0.1:{env.get('API_PORT', '8000')}"
    webapp_url = f"http://127.0.0.1:{env.get('WEBAPP_PORT', '5001')}"
    mcp_url = f"http://127.0.0.1:{env.get('MCP_PORT', '8080')}"
    public_url = env.get("DOCRAY_PUBLIC_BASE_URL", "")
    return [
        check_env(env),
        check_posture(env),
        check_database(env),
        check_corpus_rollups(env),
        check_object_store(env),
        check_api(api_url),
        check_webapp(webapp_url),
        check_mcp(mcp_url),
        check_tunnel(),
        check_public(public_url),
        check_mcp_public(public_url),
        check_smtp(env, recipient=recipient, send=send),
    ]


def exit_code(results: list[CheckResult]) -> int:
    """Return 0 when everything passed, 1 for warnings only, 2 for any failure."""
    return max((_SEVERITY.get(r.status, 0) for r in results), default=0)


def render_json(results: list[CheckResult]) -> str:
    """Serialize the report for automation."""
    worst = {0: OK, 1: WARN, 2: FAIL}[exit_code(results)]
    payload = {
        "status": worst,
        "checks": [{"name": r.name, "status": r.status, "detail": r.detail} for r in results],
    }
    return json.dumps(payload, indent=2)


def render_text(results: list[CheckResult]) -> str:
    """Render the report as an aligned, operator-readable table."""
    marks = {OK: "PASS", WARN: "WARN", FAIL: "FAIL", SKIP: "SKIP"}
    width = max(len(r.name) for r in results)
    lines = [f"  {marks[r.status]}  {r.name.ljust(width)}  {r.detail}" for r in results]
    verdict = {
        0: "prod-check: PASSED",
        1: "prod-check: PASSED WITH WARNINGS",
        2: "prod-check: FAILED",
    }
    return "\n".join(lines) + "\n\n" + verdict[exit_code(results)]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prod_check",
        description="Verify a production deployment, including a real SMTP delivery test.",
    )
    parser.add_argument(
        "--env-file",
        default=".env.prod",
        help="Env file that defines the deployment (default: .env.prod). It is authoritative.",
    )
    parser.add_argument("--to", help="Recipient for the SMTP delivery test.")
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="Verify SMTP reachability, TLS, and credentials without delivering a message.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the production checks and print the report.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv``).

    Returns:
        Process exit code: 0 clean, 1 warnings, 2 failures, 3 unreadable env file.
    """
    args = _build_parser().parse_args(argv)
    path = Path(args.env_file)
    if not path.is_file():
        print(f"error: env file not found: {path}", file=sys.stderr)
        return 3
    # File wins over the ambient shell so a development export cannot retarget
    # the check; os.environ still supplies keys the file omits.
    env = {**os.environ, **load_env_file(path)}

    results = run_checks(env, recipient=args.to, send=not args.no_send)
    if args.json:
        print(render_json(results))
    else:
        print(f"\n  doc-ray production check — {path} — {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}\n")
        print(render_text(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
