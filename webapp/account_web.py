"""Account settings routes: profile, password, email change, and preferences.

A signed-in principal edits its own data here. Reads and writes flow through the
domain services in ``doc_ray.application.account_settings`` /
``account_preferences`` over a privileged application connection (the settings
tables sit outside RLS — see 079_account_settings.sql).

Patch-sensitive helpers (``DATABASE_URL``, ``_verification_mailer``) resolve
through the ``webapp.app`` module object at call time, matching the other
D-015 route modules so the test suite's monkeypatching keeps reaching here.
"""

from __future__ import annotations

import os
from types import ModuleType

from flask import g, redirect, request, session, url_for

from doc_ray.adapters.account_preferences_postgres import PostgresPreferencesRepository
from doc_ray.adapters.account_settings_postgres import PostgresAccountRepository
from doc_ray.application.account_preferences import PreferencesService
from doc_ray.application.account_settings import (
    AccountService,
    DisplayNameInvalid,
    EmailAlreadyRegistered,
    EmailUnchanged,
    ExternalIdentityImmutable,
    InvalidCredentials,
    LocalAuthError,
    VerificationTokenExpired,
    VerificationTokenInvalid,
)

try:
    from webapp import auth_web
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    import auth_web  # type: ignore[no-redef]

_core: ModuleType
_ACCOUNT_SERVICE: AccountService | None = None
_PREFERENCES_SERVICE: PreferencesService | None = None

#: Flask-session keys used to carry one-shot banners across the POST→redirect→GET.
_OK_KEY = "settings_ok"
_ERR_KEY = "settings_error"
_TOKEN_KEY = "settings_email_token"


def _account_service() -> AccountService:
    """Return the process-wide account service, building it on first use."""
    global _ACCOUNT_SERVICE
    if _ACCOUNT_SERVICE is None:
        _ACCOUNT_SERVICE = AccountService(
            PostgresAccountRepository(_core.DATABASE_URL),
            email_change_ttl=_email_change_ttl(),
        )
    return _ACCOUNT_SERVICE


def _preferences_service() -> PreferencesService:
    """Return the process-wide preferences service, building it on first use."""
    global _PREFERENCES_SERVICE
    if _PREFERENCES_SERVICE is None:
        _PREFERENCES_SERVICE = PreferencesService(PostgresPreferencesRepository(_core.DATABASE_URL))
    return _PREFERENCES_SERVICE


def _email_change_ttl():
    from datetime import timedelta

    return timedelta(
        seconds=int(os.environ.get("DOCRAY_AUTH_EMAIL_VERIFICATION_TTL_SECONDS", "86400"))
    )


def current_preferences():
    """Return the request principal's preferences (defaults when anonymous).

    Cached on ``g`` so the theme context processor and any read route in the
    same request share a single lookup. Never raises: a lookup failure degrades
    to defaults rather than breaking an otherwise-serviceable page.
    """
    from doc_ray.application.account_preferences import AccountPreferences

    cached = getattr(g, "_account_prefs", None)
    if cached is not None:
        return cached
    prefs = AccountPreferences()
    principal_id = _settings_principal_id()
    if principal_id:
        try:
            prefs = _preferences_service().get(principal_id)
        except Exception:  # noqa: BLE001 - preferences are cosmetic; never fail the page.
            prefs = AccountPreferences()
    g._account_prefs = prefs
    return prefs


def inject_user_theme():
    """Expose the account theme default to every template (base.html pre-paint)."""
    try:
        return {"user_theme": current_preferences().theme}
    except Exception:  # noqa: BLE001 - context processors must not break rendering.
        return {"user_theme": "system"}


def _settings_principal_id() -> str | None:
    """Resolve the principal whose settings this request may edit."""
    return _core._request_principal_id()


def _require_principal():
    """Return the editing principal id, or a redirect response when absent."""
    principal_id = _settings_principal_id()
    if principal_id:
        return principal_id, None
    if auth_web._web_auth_required():
        return None, redirect(url_for("login"))
    return None, ("account settings require a configured principal", 404)


def _pop_banner() -> tuple[str | None, str | None, str | None]:
    return (
        session.pop(_OK_KEY, None),
        session.pop(_ERR_KEY, None),
        session.pop(_TOKEN_KEY, None),
    )


def settings():
    """Render the account settings screen for the signed-in principal."""
    principal_id, bail = _require_principal()
    if bail is not None:
        return bail
    account = _account_service().account(principal_id)
    prefs = _preferences_service().get(principal_id)
    ok, error, email_token = _pop_banner()
    return _core.render_template(
        "settings.html",
        account=account,
        prefs=prefs,
        notice=ok,
        error=error,
        email_token=email_token,
        local_auth_enabled=auth_web._local_auth_enabled(),
    )


def settings_profile():
    """Update the display name."""
    principal_id, bail = _require_principal()
    if bail is not None:
        return bail
    try:
        name = _account_service().update_display_name(
            principal_id=principal_id, display_name=request.form.get("display_name", "")
        )
        session[_OK_KEY] = f"Display name updated to “{name}”."
    except DisplayNameInvalid:
        session[_ERR_KEY] = "Display name cannot be empty."
    except LocalAuthError as exc:
        session[_ERR_KEY] = str(exc)
    return redirect(url_for("settings"))


def settings_password():
    """Change the local account password after re-verifying the current one."""
    principal_id, bail = _require_principal()
    if bail is not None:
        return bail
    new_password = request.form.get("new_password", "")
    if new_password != request.form.get("confirm_password", ""):
        session[_ERR_KEY] = "New password and confirmation do not match."
        return redirect(url_for("settings"))
    try:
        _account_service().change_password(
            principal_id=principal_id,
            current_password=request.form.get("current_password", ""),
            new_password=new_password,
        )
        session[_OK_KEY] = "Password changed."
    except InvalidCredentials:
        session[_ERR_KEY] = "Current password is incorrect."
    except ExternalIdentityImmutable:
        session[_ERR_KEY] = "Password is managed by your identity provider."
    except ValueError as exc:
        session[_ERR_KEY] = str(exc)
    except LocalAuthError as exc:
        session[_ERR_KEY] = str(exc)
    return redirect(url_for("settings"))


def settings_email():
    """Begin an email change by mailing a confirmation link to the new address."""
    principal_id, bail = _require_principal()
    if bail is not None:
        return bail
    new_email = request.form.get("new_email", "").strip()
    # Fail closed BEFORE minting a pending request when there is no way to
    # deliver the confirmation link — otherwise the settings page would show a
    # "change pending" banner for a token the user can never receive.
    mailer = _core._verification_mailer()
    expose_token = auth_web._env_bool("DOCRAY_AUTH_EXPOSE_VERIFICATION_TOKEN", False)
    if mailer is None and not expose_token:
        session[_ERR_KEY] = "Email change is unavailable: no email delivery is configured."
        return redirect(url_for("settings"))
    try:
        change = _account_service().request_email_change(
            principal_id=principal_id, new_email=new_email
        )
    except EmailUnchanged:
        session[_ERR_KEY] = "That is already your email address."
        return redirect(url_for("settings"))
    except EmailAlreadyRegistered:
        session[_ERR_KEY] = "That email address is already in use."
        return redirect(url_for("settings"))
    except ExternalIdentityImmutable:
        session[_ERR_KEY] = "Email is managed by your identity provider."
        return redirect(url_for("settings"))
    except (ValueError, LocalAuthError) as exc:
        session[_ERR_KEY] = str(exc)
        return redirect(url_for("settings"))

    if mailer is not None:
        if _send_email_change(mailer, change.new_email, change.token):
            session[_OK_KEY] = (
                f"Confirmation link sent to {change.new_email}. "
                "Your email changes once you open it."
            )
        else:
            session[_ERR_KEY] = "Could not send the confirmation email. Please try again."
    else:
        session[_TOKEN_KEY] = change.token
        session[_OK_KEY] = f"Confirm the change to {change.new_email} with the token below."
    return redirect(url_for("settings"))


def settings_email_confirm():
    """Public, token-gated confirmation of a pending email change."""
    token = request.args.get("token", "").strip()
    if not token:
        session[_ERR_KEY] = "Missing confirmation token."
        return redirect(url_for("settings"))
    try:
        result = _account_service().confirm_email_change(token)
    except VerificationTokenExpired:
        session[_ERR_KEY] = "That confirmation link has expired. Request the change again."
        return redirect(url_for("settings"))
    except (VerificationTokenInvalid, EmailAlreadyRegistered, LocalAuthError):
        session[_ERR_KEY] = "That confirmation link is invalid or already used."
        return redirect(url_for("settings"))
    session[_OK_KEY] = f"Email address changed to {result.new_email}."
    return redirect(url_for("settings"))


def settings_preferences():
    """Persist theme, default reader view, and rows-per-page preferences."""
    principal_id, bail = _require_principal()
    if bail is not None:
        return bail
    _preferences_service().set(
        principal_id=principal_id,
        theme=request.form.get("theme"),
        default_reader_view=request.form.get("default_reader_view"),
        rows_per_page=request.form.get("rows_per_page"),
    )
    # Drop any cached copy so this same request's context processor can't serve
    # a stale theme on the redirect target's first paint.
    if hasattr(g, "_account_prefs"):
        del g._account_prefs
    session[_OK_KEY] = "Preferences saved."
    return redirect(url_for("settings"))


def _confirm_url(token: str) -> str:
    """Return the absolute email-change confirmation link for *token*."""
    base = os.environ.get("DOCRAY_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/settings/email/confirm?token={token}"
    return url_for("settings_email_confirm", token=token, _external=True)


def _email_change_email(confirm_url: str, ttl_hours: int) -> tuple[str, str]:
    """Return the (subject, body) for an email-change confirmation message."""
    subject = "Confirm your new doc-ray email address"
    body = (
        "Hello,\n\n"
        "Someone requested to change a doc-ray account's email address to this\n"
        "one. To confirm the change, open the link below:\n\n"
        f"    {confirm_url}\n\n"
        f"The link expires in {ttl_hours} hours. If you did not request this,\n"
        "you can ignore this message — no change will be made.\n"
    )
    return subject, body


def _send_email_change(mailer, new_email: str, token: str) -> bool:
    """Deliver the email-change confirmation link; returns False on failure."""
    from flask import current_app

    from doc_ray.ports.mailer import MailerError

    ttl_hours = max(
        1, int(os.environ.get("DOCRAY_AUTH_EMAIL_VERIFICATION_TTL_SECONDS", "86400")) // 3600
    )
    subject, body = _email_change_email(_confirm_url(token), ttl_hours)
    try:
        mailer.send(to=new_email, subject=subject, body=body)
    except MailerError:
        current_app.logger.exception("email-change confirmation delivery failed for %s", new_email)
        return False
    return True


def register(app, core: ModuleType) -> None:
    """Attach the account-settings routes and hooks to *app*, late-binding *core*."""
    global _core
    _core = core
    app.context_processor(inject_user_theme)
    app.add_url_rule("/settings", "settings", settings)
    app.add_url_rule("/settings/profile", "settings_profile", settings_profile, methods=["POST"])
    app.add_url_rule("/settings/password", "settings_password", settings_password, methods=["POST"])
    app.add_url_rule("/settings/email", "settings_email", settings_email, methods=["POST"])
    app.add_url_rule("/settings/email/confirm", "settings_email_confirm", settings_email_confirm)
    app.add_url_rule(
        "/settings/preferences", "settings_preferences", settings_preferences, methods=["POST"]
    )
