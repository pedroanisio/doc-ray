"""doc-ray browser — a small Flask frontend over the annotated corpus.

Navigates the layers built by the pipeline:
    documents → sentences → lexemes → occurrences (keyword-in-context)

Read-only. Connects to the same Postgres the ingester writes to. Load the
deployment settings first so DATABASE_URL carries the real host port/password:

    set -a; source .env; set +a
    .venv/bin/flask --app webapp.app run --port 5001
or simply:
    .venv/bin/python -m webapp.app          # serves on http://localhost:5001

This module is the webapp's core: Flask app construction, the per-request DB
connection (``get_conn``/``q``/``q1``/``exec_sql``), rate limiting, and error
handling. Route groups live in sibling modules (``auth_web``, ``corpus_web``,
``document_web``, ``collections_web``, ``admin_web``) registered below (D-015);
they resolve the helpers here through this module object at call time, so the
test suite's monkeypatching of these attributes reaches every route.
"""

from __future__ import annotations

import functools
import hashlib
import os
import secrets
import sys
from pathlib import Path

import psycopg
from flask import Flask, g, make_response, render_template, request, url_for
from psycopg import sql
from psycopg.rows import dict_row

# Re-exported for the test suite, which accesses these as webapp.app attributes.
from doc_ray.application.local_auth import InvalidCredentials as InvalidCredentials
from doc_ray.config import DEFAULT_DATABASE_URL

try:
    from webapp import (
        account_web,
        admin_web,
        auth_web,
        collections_web,
        corpus_web,
        document_web,
        oauth_web,
        overlay_web,
    )
    from webapp.auth_session import (
        AuthenticatedWebPrincipal as AuthenticatedWebPrincipal,
    )
    from webapp.auth_session import (
        auth_sessions as auth_sessions,
    )
    from webapp.auth_session import (
        session_cookie_secure,
    )
    from webapp.csrf import csrf_token, require_csrf_token
    from webapp.graphql_client import gql as gql
    from webapp.rate_limit import FixedWindowRateLimiter, rate_limit_rule_from_env
    from webapp.security_headers import apply_security_headers
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    import account_web
    import admin_web
    import auth_web
    import collections_web
    import corpus_web
    import document_web
    import oauth_web
    import overlay_web
    from auth_session import (
        AuthenticatedWebPrincipal as AuthenticatedWebPrincipal,
    )
    from auth_session import (
        auth_sessions as auth_sessions,
    )
    from auth_session import (
        session_cookie_secure,
    )
    from csrf import csrf_token, require_csrf_token
    from graphql_client import gql as gql
    from rate_limit import FixedWindowRateLimiter, rate_limit_rule_from_env
    from security_headers import apply_security_headers

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

# Row-Level Security enforcement (see db/migrations/018_rbac.sql). The superuser
# connection BYPASSES RLS by design. To actually enforce document-level access
# for end users, set DOCRAY_PRINCIPAL_ID: the app then drops to the non-superuser
# role (so policies apply) and stamps the request principal. Unset => superuser
# passthrough (local/dev only — no access control).
DB_ROLE = os.environ.get("DOCRAY_DB_ROLE", "docray_client")
PRINCIPAL_ID = os.environ.get("DOCRAY_PRINCIPAL_ID") or None


def _assert_db_role_resolves_principals(db_role: str, *, browser_auth_required: bool) -> None:
    """Refuse to boot when per-user browser auth cannot resolve request principals.

    Browser auth stamps each request with the logged-in principal via
    ``app.principal_id``, which ``docray_current_principal()`` honours ONLY for
    the ``docray_api`` role. With any other role (e.g. the ``docray_client``
    default) every authenticated session resolves to a NULL principal, so RLS
    hides all documents and jobs — a silently-empty corpus and 404 job pages.
    Failing loudly here turns that silent misconfiguration into an obvious
    boot failure (an unhealthy container) instead.
    """
    if browser_auth_required and db_role != "docray_api":
        raise RuntimeError(
            f"DOCRAY_DB_ROLE={db_role!r} cannot resolve per-session principals while "
            "browser auth is enabled: only 'docray_api' honours app.principal_id for "
            "RLS, so any other role serves an empty corpus. Set DOCRAY_DB_ROLE=docray_api."
        )


_assert_db_role_resolves_principals(DB_ROLE, browser_auth_required=auth_web.web_auth_required())

app = Flask(__name__)
# Uploads are forwarded to the API, which buffers the file for hashing — cap
# the request size here so an oversized POST is rejected at the front door.
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("DOCRAY_MAX_UPLOAD_MB", "200")) * 1024 * 1024
# Public marketing landing at the root. The corpus browser now lives at
# /corpus (endpoint name "documents" is unchanged, so url_for keeps working).
# "landing" is exempt from the webapp auth gate — it is the pre-auth front door.
app.add_url_rule("/", "landing", lambda: render_template("landing.html"))
app.add_url_rule("/help", "help_page", lambda: render_template("help.html"))
# Signs the session cookie that carries the CSRF token below; stable for the life
# of one process even when unset (dev-only — set explicitly for a real deployment).
app.secret_key = os.environ.get("DOCRAY_WEBAPP_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = session_cookie_secure()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.jinja_env.globals["csrf_token"] = csrf_token


@functools.cache
def _asset_version(filename: str) -> str:
    """Return a short content hash of a static file, for cache-busting.

    Static assets are baked into the image and immutable for the life of the
    process, so hashing once and caching the result is safe. A *content* hash
    (not mtime) means the token changes only when the bytes change, so a deploy
    that ships new HTML + CSS/JS together invalidates the browser's cached copy
    in lockstep. Missing files degrade to ``"0"`` rather than raising, so a
    typo'd asset never 500s a page.
    """
    path = Path(app.static_folder or "") / filename
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"


def static_asset(filename: str) -> str:
    """Content-versioned static URL (``?v=<hash>``) that survives cache TTLs.

    Without this, a stylesheet/script change deployed alongside new markup is
    masked by the browser's cached older asset — the root cause of the footer
    rendering unstyled after a deploy.
    """
    return url_for("static", filename=filename, v=_asset_version(filename))


app.jinja_env.globals["static_asset"] = static_asset

_RATE_LIMITER = FixedWindowRateLimiter()


def reset_rate_limits() -> None:
    """Clear webapp rate-limit counters; used by tests and local diagnostics."""
    _RATE_LIMITER.reset()


def _client_identifier() -> str:
    """Return the caller identity used for per-client rate limiting."""
    return request.access_route[0] if request.access_route else (request.remote_addr or "unknown")


def _rate_limit_response(retry_after_seconds: int):
    """Build the shared 429 response with a Retry-After header."""
    response = make_response("Too many requests. Please retry later.\n", 429)
    response.headers["Retry-After"] = str(retry_after_seconds)
    return response


def enforce_webapp_rate_limits():
    """Throttle expensive unauthenticated webapp routes per client."""
    route = None
    rule = None
    if request.endpoint == "search":
        route = "search"
        rule = rate_limit_rule_from_env(
            os.environ,
            "DOCRAY_WEBAPP_SEARCH_RATE_LIMIT",
            default_requests=60,
            default_window_seconds=60,
        )
    elif request.endpoint == "upload_submit":
        route = "upload"
        rule = rate_limit_rule_from_env(
            os.environ,
            "DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT",
            default_requests=10,
            default_window_seconds=60,
        )
    elif request.endpoint == "resend_verification":
        # Sends outbound mail on behalf of arbitrary input — throttle hard.
        route = "resend_verification"
        rule = rate_limit_rule_from_env(
            os.environ,
            "DOCRAY_WEBAPP_RESEND_RATE_LIMIT",
            default_requests=5,
            default_window_seconds=3600,
        )
    elif request.endpoint == "login_submit" and request.method == "POST":
        # Online brute-force guard; the account-level lockout in
        # LocalAuthService is the second layer.
        route = "login"
        rule = rate_limit_rule_from_env(
            os.environ,
            "DOCRAY_WEBAPP_LOGIN_RATE_LIMIT",
            default_requests=10,
            default_window_seconds=300,
        )
    elif request.endpoint == "register_submit" and request.method == "POST":
        route = "register"
        rule = rate_limit_rule_from_env(
            os.environ,
            "DOCRAY_WEBAPP_REGISTER_RATE_LIMIT",
            default_requests=5,
            default_window_seconds=3600,
        )
    if route is None or rule is None:
        return None
    result = _RATE_LIMITER.check(route, _client_identifier(), rule)
    if not result.allowed:
        return _rate_limit_response(result.retry_after_seconds)
    return None


app.before_request(enforce_webapp_rate_limits)
app.before_request(require_csrf_token)
app.after_request(apply_security_headers)


# --- per-request DB connection --------------------------------------------


def _apply_principal(conn) -> None:
    """Enforce RLS on ``conn`` by dropping role and setting the request principal.

    No-op when no principal is configured. Trust boundary: the principal comes
    from server-side config (the trusted app tier), never from user input — the
    GUC-based model is only safe under that assumption. Mapping an authenticated
    end user to their principal, and per-principal LOGIN roles to defend against
    hostile direct SQL, are the next steps (see the ``018_rbac.sql`` header).

    Args:
        conn: The open ``psycopg`` connection to configure in place.
    """
    principal_id = _request_principal_id()
    if not principal_id:
        return
    with conn.cursor() as cur:
        # DB_ROLE is an operator-set identifier (never user input) — quote it safely.
        cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(DB_ROLE)))
        # SET cannot bind a value, so use set_config to pass the principal safely.
        cur.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))


def get_conn():
    """Return the request-scoped Postgres connection, opening it on first use.

    On first open the connection is configured for RLS enforcement when a
    principal is set (see :func:`_apply_principal`).

    Autocommit matters (BL-131): teardown closes this connection, and closing
    a psycopg connection with an open implicit transaction rolls it back —
    the direct collection writes were silently discarded after redirecting.
    Every other connection in the codebase (worker, CLIs, tests) is already
    autocommit; reads are unaffected.

    Returns:
        The ``psycopg`` connection stored on Flask's request-global ``g``.
    """
    if "conn" not in g:
        g.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
        _apply_principal(g.conn)
    return g.conn


@app.teardown_appcontext
def _close_conn(_exc):
    """Close the request-scoped connection at the end of the request."""
    conn = g.pop("conn", None)
    if conn is not None:
        conn.close()


def q(sql, params=()):
    """Run a query and return all rows.

    Args:
        sql: The SQL statement to execute.
        params: Bind parameters passed through to the cursor.

    Returns:
        A list of ``dict_row`` mappings, one per result row.
    """
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def q1(sql, params=()):
    """Run a query and return the first row, or ``None`` if there are none.

    Args:
        sql: The SQL statement to execute.
        params: Bind parameters passed through to the cursor.

    Returns:
        The first ``dict_row`` mapping, or ``None`` when the query yields no rows.
    """
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def exec_sql(sql, params=()):
    """Run a statement that does not return rows."""
    with get_conn().cursor() as cur:
        cur.execute(sql, params)


# --- error handlers ---------------------------------------------------------


@app.errorhandler(413)
def _upload_too_large(_exc):
    """Redirect oversized uploads back to the form with a friendly message."""
    from flask import redirect, url_for

    limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    return redirect(url_for("upload_form", error=f"file exceeds the {limit_mb} MB upload limit"))


@app.errorhandler(psycopg.OperationalError)
def _database_unavailable(exc: psycopg.OperationalError):
    """Render a graceful page instead of leaking the raw Werkzeug debugger.

    ``OperationalError`` covers both "server unreachable" and "authentication
    failed" — anything meaning the connection itself couldn't be established,
    as opposed to a query-level bug. Unhandled, this would otherwise surface
    a full interactive traceback (and, in debug mode, a code-execution
    console) to whoever hits any DB-touching route.

    Args:
        exc: The connection failure raised by ``psycopg.connect``.

    Returns:
        A rendered error page with a 503 status.
    """
    app.logger.error("database connection failed: %s", exc)
    return render_template("error.html", detail=str(exc)), 503


#: User-facing titles/fallbacks for the shared HTTP error envelope. Keyed by
#: status; anything unlisted falls back to the 500 copy.
_HTTP_ERROR_TITLES = {
    400: "Bad request",
    401: "Sign-in required",
    403: "Not allowed",
    404: "Page not found",
    405: "Method not allowed",
    500: "Something went wrong",
}
_HTTP_ERROR_FALLBACK = {
    400: "That request couldn't be processed. Check the form and try again.",
    401: "You need to sign in to view this.",
    403: "You don't have access to this.",
    404: "We couldn't find what you were looking for. It may have been moved or removed.",
    405: "That action isn't allowed on this page.",
    500: "An unexpected error occurred on our side. Please try again.",
}


def _render_http_error(exc: object) -> tuple[str, int]:
    """Render any 4xx/5xx through one styled envelope (base.html), no leaks.

    A specific ``abort(code, "message")`` shows that message for 4xx; generic
    aborts fall back to friendly copy. 5xx never echoes the exception (which may
    carry internals) — it always uses the fixed 500 copy.

    Args:
        exc: The HTTPException (or, for a 500, the raw exception) Flask caught.

    Returns:
        The rendered error page and its status code.
    """
    from flask import url_for
    from werkzeug.exceptions import HTTPException

    code = exc.code if isinstance(exc, HTTPException) and exc.code else 500
    title = _HTTP_ERROR_TITLES.get(code, _HTTP_ERROR_TITLES[500])
    if code >= 500:
        message = _HTTP_ERROR_FALLBACK[500]
    else:
        description = getattr(exc, "description", None)
        is_generic = not description or (
            isinstance(exc, HTTPException) and description == type(exc).description
        )
        message = _HTTP_ERROR_FALLBACK.get(code, _HTTP_ERROR_FALLBACK[500])
        if not is_generic:
            message = description
    try:
        back_url = url_for("documents")
    except Exception:  # noqa: BLE001 - url building must never mask the real error
        back_url = "/"
    return (
        render_template(
            "http_error.html",
            code=code,
            title=title,
            message=message,
            back_url=back_url,
            back_label="Back to the corpus",
        ),
        code,
    )


for _http_error_code in (400, 401, 403, 404, 405, 500):
    app.register_error_handler(_http_error_code, _render_http_error)


# --- route groups (D-015 split) ---------------------------------------------
#
# Each sibling module attaches its routes here and resolves this module's
# helpers (q/q1/exec_sql/gql/render_template/PRINCIPAL_ID/...) late-bound, so
# monkeypatching webapp.app attributes still reaches every moved route.

_SELF = sys.modules[__name__]
overlay_web.bind(_SELF)
auth_web.register(app, _SELF)
corpus_web.register(app, _SELF)
document_web.register(app, _SELF)
collections_web.register(app, _SELF)
admin_web.register(app, _SELF)
account_web.register(app, _SELF)
oauth_web.register(app, _SELF)

# Per-principal UI preferences, read by the corpus/reader routes and the theme
# context processor; late-bound like the other cross-module helpers.
current_preferences = account_web.current_preferences

# Names the test suite (and templates) reach as webapp.app attributes; the
# canonical definitions moved to the sibling route modules.
_request_principal_id = auth_web._request_principal_id
_verification_mailer = auth_web._verification_mailer
register_local_user = auth_web.register_local_user
resend_local_verification = auth_web.resend_local_verification
complete_local_login = auth_web.complete_local_login
verify_local_email = auth_web.verify_local_email
complete_oidc_login = auth_web.complete_oidc_login
complete_saml_login = auth_web.complete_saml_login
documents = corpus_web.documents
document = document_web.document
lexemes = corpus_web.lexemes
lexeme = corpus_web.lexeme
search = corpus_web.search
_markup_highlights = overlay_web.markup_highlights
_apply_markup_highlights = overlay_web.apply_markup_highlights


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5001")), debug=True)
