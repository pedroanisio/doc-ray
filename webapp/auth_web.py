"""Browser auth: session helpers plus login/registration/verification routes.

Split out of ``webapp/app.py`` (D-015). Shared state and patch-sensitive
helpers (``PRINCIPAL_ID``, ``DATABASE_URL``, ``_verification_mailer``,
``register_local_user``, ``complete_local_login``, ``verify_local_email``,
``resend_local_verification``) are resolved through the ``webapp.app`` module
object at call time so the test suite's monkeypatching of those attributes
keeps reaching the routes that moved here.
"""

from __future__ import annotations

import os
import secrets
from datetime import timedelta
from types import ModuleType

import psycopg
from flask import abort, current_app, redirect, request, session, url_for
from psycopg.rows import dict_row

from doc_ray.adapters.local_auth_postgres import PostgresLocalAuthRepository
from doc_ray.application.local_auth import (
    DisabledPrincipal,
    EmailAlreadyRegistered,
    EmailNotVerified,
    InvalidCredentials,
    LocalAuthError,
    LocalAuthService,
    VerificationTokenExpired,
    VerificationTokenInvalid,
)

try:
    from webapp.api_jwt import mint_forwardable_api_jwt
    from webapp.auth_config import (
        env_bool,
        local_auth_enabled,
        local_registration_enabled,
        web_auth_required,
    )
    from webapp.auth_session import (
        AuthenticatedWebPrincipal,
        auth_sessions,
        oidc_authorization_redirect,
        oidc_claims_from_code,
        saml_assertion_from_response,
        saml_login_redirect,
    )
    from webapp.login_redirect import post_login_destination
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from api_jwt import mint_forwardable_api_jwt
    from auth_config import (
        env_bool,
        local_auth_enabled,
        local_registration_enabled,
        web_auth_required,
    )
    from auth_session import (
        AuthenticatedWebPrincipal,
        auth_sessions,
        oidc_authorization_redirect,
        oidc_claims_from_code,
        saml_assertion_from_response,
        saml_login_redirect,
    )
    from login_redirect import post_login_destination

_core: ModuleType
_LOCAL_AUTH_SERVICE: LocalAuthService | None = None

#: Endpoints reachable without an authenticated web session.
_AUTH_PUBLIC_ENDPOINTS = {
    "static",
    "landing",
    "login",
    "login_submit",
    "register_form",
    "register_submit",
    "verify_email_form",
    "resend_verification",
    "oidc_callback",
    "saml_callback",
    "logout",
    # Email-change confirmation is proven by the token mailed to the new
    # address, exactly like registration verification — reachable without a
    # live session so the link works from any browser.
    "settings_email_confirm",
}


def _auth_state() -> str:
    """Mint and store a login CSRF state token."""
    state = secrets.token_urlsafe(32)
    session["_auth_state"] = state
    return state


def _validate_auth_state(sent: str | None) -> None:
    """Abort with 400 unless *sent* matches the stored login state token."""
    expected = session.get("_auth_state")
    if not sent or not expected or sent != expected:
        abort(400, "invalid login state")
    session.pop("_auth_state", None)


def _resolve_web_principal(external_subject: str) -> str:
    """Resolve an external login subject to a doc-ray principal id."""
    with (
        psycopg.connect(_core.DATABASE_URL, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            "SELECT id FROM principals WHERE external_subject = %s LIMIT 1",
            (external_subject,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("external subject is not mapped to a principal")
    return str(row["id"])


def complete_oidc_login(code: str, redirect_uri: str) -> AuthenticatedWebPrincipal:
    """Exchange an OIDC authorization code and resolve the authenticated principal.

    The verified ``id_token`` is retained as ``bearer_token`` so proxied API
    calls (page images, downloads) carry the user's own credential and RLS
    resolves to their principal — see ``graphql_client.api_auth_headers``.
    """
    claims, id_token = oidc_claims_from_code(code=code, redirect_uri=redirect_uri)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ValueError("OIDC sub is required")
    return AuthenticatedWebPrincipal(
        principal_id=_resolve_web_principal(subject),
        subject=f"oidc:{subject}",
        bearer_token=id_token,
    )


def complete_saml_login(saml_response: str) -> AuthenticatedWebPrincipal:
    """Validate a SAML response and resolve the authenticated principal.

    SAML yields a validated assertion, not an API-acceptable bearer JWT, so
    ``bearer_token`` stays ``None`` and SAML-origin sessions cannot forward a
    user credential to the API. Deployments serving originals/images to SAML
    users need a webapp-minted API JWT (``DOCRAY_API_JWT_SECRET``) instead.
    """
    assertion = saml_assertion_from_response(saml_response)
    return AuthenticatedWebPrincipal(
        principal_id=_resolve_web_principal(assertion.external_subject),
        subject=assertion.external_subject,
    )


def _establish_auth_session(principal: AuthenticatedWebPrincipal) -> None:
    """Create the server-side auth session and remember its id in the cookie."""
    sid = auth_sessions.create(
        principal_id=principal.principal_id,
        subject=principal.subject,
        bearer_token=principal.bearer_token,
    )
    session["_auth_sid"] = sid


def _current_auth_session():
    """Return the live server-side auth session record, or ``None``."""
    sid = session.get("_auth_sid")
    if not sid:
        return None
    record = auth_sessions.get(sid)
    if record is None:
        session.pop("_auth_sid", None)
    return record


def _session_principal_id() -> tuple[str | None, bool]:
    """Return ``(principal_id, had_auth_session)`` for the current request."""
    sid = session.get("_auth_sid")
    if not sid:
        return None, False
    record = auth_sessions.get(sid)
    if record is None:
        session.pop("_auth_sid", None)
        return None, True
    return record.principal_id, True


def _request_principal_id() -> str | None:
    """Resolve the effective principal for the request (session, then env)."""
    principal_id, had_auth_session = _session_principal_id()
    if principal_id:
        return principal_id
    if had_auth_session:
        return None
    if web_auth_required():
        return None
    return _core.PRINCIPAL_ID


def require_webapp_auth():
    """Block anonymous browser reads when a web auth provider is configured."""
    if not web_auth_required():
        return None
    if request.endpoint in _AUTH_PUBLIC_ENDPOINTS:
        return None
    principal_id, _had_auth_session = _session_principal_id()
    if principal_id:
        return None
    if request.method in {"GET", "HEAD"}:
        return redirect(url_for("login"))
    return "webapp authentication required\n", 401


def inject_auth_navigation():
    """Expose browser-auth state to global templates."""
    return {
        "current_auth_session": _current_auth_session(),
        "web_auth_required": web_auth_required(),
        "local_registration_available": local_auth_enabled() and local_registration_enabled(),
    }


def _local_auth_service() -> LocalAuthService:
    """Return the process-wide local-auth service, building it on first use."""
    global _LOCAL_AUTH_SERVICE
    if _LOCAL_AUTH_SERVICE is None:
        _LOCAL_AUTH_SERVICE = LocalAuthService(
            PostgresLocalAuthRepository(_core.DATABASE_URL),
            verification_ttl=timedelta(
                seconds=int(os.environ.get("DOCRAY_AUTH_EMAIL_VERIFICATION_TTL_SECONDS", "86400"))
            ),
            max_failed_logins=int(os.environ.get("DOCRAY_AUTH_MAX_FAILED_LOGINS", "10")),
            lockout_seconds=int(os.environ.get("DOCRAY_AUTH_LOCKOUT_SECONDS", "900")),
        )
    return _LOCAL_AUTH_SERVICE


def register_local_user(email: str, password: str, display_name: str | None = None):
    """Register a self-service user pending email verification."""
    return _local_auth_service().register(
        email=email,
        password=password,
        display_name=display_name,
    )


def resend_local_verification(email: str) -> str | None:
    """Reissue a verification token for a pending registration (or None)."""
    return _local_auth_service().resend_verification(email=email)


def _verification_mailer():
    """Return the configured outbound mailer, or None when SMTP is unset."""
    from doc_ray.adapters.mailer_smtp import SmtpMailer

    return SmtpMailer.from_env()


def _verification_url(token: str) -> str:
    """Return the absolute verification link for *token*."""
    base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/verify-email?token={token}"
    return url_for("verify_email_form", token=token, _external=True)


def _send_verification_email(mailer, email_normalized: str, token: str) -> bool:
    """Deliver the verification link; returns False when sending failed."""
    from doc_ray.adapters.mailer_smtp import verification_email
    from doc_ray.ports.mailer import MailerError

    ttl_hours = max(
        1, int(os.environ.get("DOCRAY_AUTH_EMAIL_VERIFICATION_TTL_SECONDS", "86400")) // 3600
    )
    subject, body = verification_email(_verification_url(token), ttl_hours=ttl_hours)
    try:
        mailer.send(to=email_normalized, subject=subject, body=body)
    except MailerError:
        current_app.logger.exception("verification email delivery failed for %s", email_normalized)
        return False
    return True


def complete_local_login(email: str, password: str) -> AuthenticatedWebPrincipal:
    """Authenticate a verified local user for a browser session."""
    principal = _local_auth_service().login(email=email, password=password)
    if principal.principal_id is None or principal.subject is None:
        raise ValueError("local login did not resolve a principal")
    return AuthenticatedWebPrincipal(
        principal_id=principal.principal_id,
        subject=principal.subject,
        bearer_token=mint_forwardable_api_jwt(
            principal.principal_id, principal.subject, principal.scopes
        ),
    )


def verify_local_email(token: str) -> AuthenticatedWebPrincipal:
    """Consume an email verification token and resolve a browser principal."""
    result = _local_auth_service().verify_email(token)
    subject = f"local:{result.email_normalized}"
    # Registered users hold the reader role; mint a read-scoped forwardable token
    # so the post-verification auto-login can render previews/originals too.
    return AuthenticatedWebPrincipal(
        principal_id=result.principal_id,
        subject=subject,
        bearer_token=mint_forwardable_api_jwt(result.principal_id, subject, frozenset({"read"})),
    )


def login():
    """Redirect the browser to the configured OIDC or SAML IdP."""
    state = _auth_state()
    oidc_url = oidc_authorization_redirect(
        redirect_uri=url_for("oidc_callback", _external=True),
        state=state,
    )
    if oidc_url:
        return redirect(oidc_url)
    saml_url = saml_login_redirect(state=state)
    if saml_url:
        return redirect(saml_url)
    if local_auth_enabled():
        return _core.render_template(
            "login.html",
            registration_enabled=local_registration_enabled(),
            email=request.args.get("email", ""),
        )
    return "webapp login is not configured", 404


def login_submit():
    """Complete local email/password login and establish the web session."""
    if not local_auth_enabled():
        return "webapp login is not configured", 404
    email = request.form.get("email", "")
    try:
        principal = _core.complete_local_login(email, request.form.get("password", ""))
    except EmailNotVerified:
        return (
            _core.render_template(
                "login.html",
                registration_enabled=local_registration_enabled(),
                email=email,
                error="Email verification is required before login.",
            ),
            403,
        )
    except (InvalidCredentials, DisabledPrincipal):
        return (
            _core.render_template(
                "login.html",
                registration_enabled=local_registration_enabled(),
                email=email,
                error="Invalid email or password.",
            ),
            401,
        )
    except LocalAuthError as exc:
        return (
            _core.render_template(
                "login.html",
                registration_enabled=local_registration_enabled(),
                email=email,
                error=str(exc),
            ),
            400,
        )
    _establish_auth_session(principal)
    return redirect(post_login_destination())


def register_form():
    """Render the local registration form."""
    if not (local_auth_enabled() and local_registration_enabled()):
        return "webapp registration is not configured", 404
    return _core.render_template("register.html")


def register_submit():
    """Register a local account and deliver its email-verification link."""
    if not (local_auth_enabled() and local_registration_enabled()):
        return "webapp registration is not configured", 404
    email = request.form.get("email", "")
    display_name = request.form.get("display_name") or None
    mailer = _core._verification_mailer()
    expose_token = env_bool("DOCRAY_AUTH_EXPOSE_VERIFICATION_TOKEN", False)
    if mailer is None and not expose_token:
        # Fail closed BEFORE creating the account: with no delivery mechanism
        # the token would be discarded (only its hash is stored) and the
        # address permanently stranded, unable to verify or ever re-register.
        return (
            _core.render_template(
                "register.html",
                email=email,
                error=(
                    "Registration is unavailable: no verification email "
                    "delivery is configured (set DOCRAY_SMTP_HOST)."
                ),
            ),
            503,
        )
    try:
        result = _core.register_local_user(email, request.form.get("password", ""), display_name)
    except EmailAlreadyRegistered:
        return (
            _core.render_template(
                "register.html", email=email, error="Email is already registered."
            ),
            409,
        )
    except ValueError as exc:
        return _core.render_template("register.html", email=email, error=str(exc)), 400
    session["pending_verification_email"] = result.email_normalized
    if expose_token:
        session["pending_verification_token"] = result.verification_token
    if mailer is not None and not _send_verification_email(
        mailer, result.email_normalized, result.verification_token
    ):
        session["pending_verification_send_failed"] = True
    return redirect(url_for("verify_email_form"))


def verify_email_form():
    """Verify local email tokens or render the pending-verification page."""
    if not local_auth_enabled():
        return "webapp email verification is not configured", 404
    token = request.args.get("token", "").strip()
    if not token:
        return _core.render_template(
            "verify_email.html",
            email=session.get("pending_verification_email"),
            verification_token=session.get("pending_verification_token"),
            send_failed=session.pop("pending_verification_send_failed", False),
        )
    try:
        principal = _core.verify_local_email(token)
    except VerificationTokenExpired:
        return _core.render_template("verify_email.html", error="Verification link expired."), 410
    except (VerificationTokenInvalid, ValueError):
        return (
            _core.render_template("verify_email.html", error="Verification link is invalid."),
            400,
        )
    _establish_auth_session(principal)
    session.pop("pending_verification_email", None)
    session.pop("pending_verification_token", None)
    return redirect(post_login_destination())


def resend_verification():
    """Reissue and deliver a verification link for a pending registration.

    Responds identically whether or not the address has a pending
    registration, so this endpoint cannot be used to enumerate accounts.
    """
    if not local_auth_enabled():
        return "webapp email verification is not configured", 404
    email = request.form.get("email", "").strip()
    token = _core.resend_local_verification(email) if email else None
    mailer = _core._verification_mailer() if token is not None else None
    # Report delivery failures honestly, exactly as register_submit does. This
    # reveals that a send was attempted (hence the address is pending) during an
    # SMTP outage — an accepted trade: a resend that silently "succeeds" while
    # nothing is delivered strands the very user this endpoint exists to help.
    # The anti-enumeration guarantee still holds whenever the mailer is healthy.
    send_failed = mailer is not None and not _send_verification_email(mailer, email.lower(), token)
    expose = mailer is None and env_bool("DOCRAY_AUTH_EXPOSE_VERIFICATION_TOKEN", False)
    if token is not None and expose:
        session["pending_verification_token"] = token
    return _core.render_template(
        "verify_email.html",
        email=email or session.get("pending_verification_email"),
        verification_token=session.get("pending_verification_token"),
        resent=True,
        send_failed=send_failed,
    )


def profile():
    """Back-compat redirect: the profile page is now the account settings page."""
    return redirect(url_for("settings"))


def oidc_callback():
    """Complete OIDC login and establish the server-side web session."""
    _validate_auth_state(request.args.get("state"))
    code = request.args.get("code")
    if not code:
        abort(400, "missing OIDC code")
    try:
        principal = _core.complete_oidc_login(code, url_for("oidc_callback", _external=True))
    except ValueError as exc:
        abort(401, str(exc))
    _establish_auth_session(principal)
    return redirect(post_login_destination())


def saml_callback():
    """Complete SAML login and establish the server-side web session."""
    _validate_auth_state(request.form.get("RelayState"))
    response = request.form.get("SAMLResponse", "")
    if not response:
        abort(400, "missing SAMLResponse")
    try:
        principal = _core.complete_saml_login(response)
    except ValueError as exc:
        abort(401, str(exc))
    _establish_auth_session(principal)
    return redirect(post_login_destination())


def logout():
    """Revoke the server-side auth session and clear the Flask session cookie."""
    auth_sessions.revoke(session.get("_auth_sid"))
    session.clear()
    return redirect(url_for("documents"))


def register(app, core: ModuleType) -> None:
    """Attach the browser-auth routes and hooks to *app*, late-binding *core*.

    ``core`` is the ``webapp.app`` module object; patch-sensitive helpers are
    looked up through it at call time (D-015 split, see module docstring).
    Endpoint names are preserved exactly so ``url_for`` calls and the
    rate-limit/auth endpoint checks keep working.
    """
    global _core
    _core = core
    app.before_request(require_webapp_auth)
    app.context_processor(inject_auth_navigation)
    app.add_url_rule("/login", "login", login)
    app.add_url_rule("/login", "login_submit", login_submit, methods=["POST"])
    app.add_url_rule("/register", "register_form", register_form)
    app.add_url_rule("/register", "register_submit", register_submit, methods=["POST"])
    app.add_url_rule("/verify-email", "verify_email_form", verify_email_form)
    app.add_url_rule(
        "/resend-verification", "resend_verification", resend_verification, methods=["POST"]
    )
    app.add_url_rule("/profile", "profile", profile)
    app.add_url_rule("/auth/oidc/callback", "oidc_callback", oidc_callback)
    app.add_url_rule("/auth/saml/callback", "saml_callback", saml_callback, methods=["POST"])
    app.add_url_rule("/logout", "logout", logout, methods=["POST"])
