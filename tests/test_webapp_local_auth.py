"""Flask local registration/login wiring tests."""

from __future__ import annotations

from webapp import app as webapp


def test_webapp_local_login_establishes_server_side_session(monkeypatch) -> None:
    principal_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setattr(
        webapp,
        "complete_local_login",
        lambda email, password: webapp.AuthenticatedWebPrincipal(
            principal_id=principal_id,
            subject=f"local:{email.lower()}",
        ),
    )

    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-1"
    resp = client.post(
        "/login",
        data={
            "csrf_token": "csrf-1",
            "email": "Alice@Example.com",
            "password": "correct horse battery staple",
        },
    )

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        sid = sess["_auth_sid"]
    record = webapp.auth_sessions.get(sid)
    assert record is not None
    assert record.principal_id == principal_id
    assert record.subject == "local:alice@example.com"


def test_webapp_register_does_not_login_until_email_verified(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setenv("DOCRAY_AUTH_REGISTRATION_ENABLED", "true")

    class Result:
        principal_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        email_normalized = "alice@example.com"
        verification_token = "verify-token"

    monkeypatch.setattr(webapp, "_verification_mailer", lambda: _RecordingMailer())
    monkeypatch.setattr(
        webapp,
        "register_local_user",
        lambda email, password, display_name=None: Result(),
    )

    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-1"
    resp = client.post(
        "/register",
        data={
            "csrf_token": "csrf-1",
            "email": "Alice@Example.com",
            "password": "correct horse battery staple",
            "display_name": "Alice",
        },
    )

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "_auth_sid" not in sess
        assert sess["pending_verification_email"] == "alice@example.com"


def test_webapp_verify_email_establishes_session(monkeypatch) -> None:
    principal_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setattr(
        webapp,
        "verify_local_email",
        lambda token: webapp.AuthenticatedWebPrincipal(
            principal_id=principal_id,
            subject="local:alice@example.com",
        ),
    )

    client = webapp.app.test_client()
    resp = client.get("/verify-email?token=verify-token")

    assert resp.status_code == 302
    with client.session_transaction() as sess:
        sid = sess["_auth_sid"]
    record = webapp.auth_sessions.get(sid)
    assert record is not None
    assert record.principal_id == principal_id
    assert record.subject == "local:alice@example.com"


class _RecordingMailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = fail

    def send(self, *, to: str, subject: str, body: str) -> None:
        if self.fail:
            from doc_ray.ports.mailer import MailerError

            raise MailerError("smtp relay down")
        self.sent.append((to, subject, body))


class _Registration:
    principal_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    email_normalized = "alice@example.com"
    verification_token = "verify-token"


def _auth_client(monkeypatch):
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setenv("DOCRAY_AUTH_REGISTRATION_ENABLED", "true")
    monkeypatch.delenv("DOCRAY_AUTH_EXPOSE_VERIFICATION_TOKEN", raising=False)
    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "csrf-1"
    return client


def test_webapp_register_fails_closed_when_no_verification_delivery_exists(monkeypatch) -> None:
    """Regression for the audit blocker: with no mailer and no dev token
    exposure, registration must refuse loudly BEFORE creating an account —
    never strand a user who could not possibly receive their token."""
    client = _auth_client(monkeypatch)
    monkeypatch.setattr(webapp, "_verification_mailer", lambda: None, raising=False)
    monkeypatch.setattr(
        webapp,
        "register_local_user",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("account must not be created")),
    )

    resp = client.post(
        "/register",
        data={"csrf_token": "csrf-1", "email": "a@example.com", "password": "x" * 12},
    )

    assert resp.status_code == 503
    assert "delivery" in resp.get_data(as_text=True).lower()


def test_webapp_register_emails_the_verification_link(monkeypatch) -> None:
    client = _auth_client(monkeypatch)
    mailer = _RecordingMailer()
    monkeypatch.setattr(webapp, "_verification_mailer", lambda: mailer, raising=False)
    monkeypatch.setattr(
        webapp, "register_local_user", lambda email, password, display_name=None: _Registration()
    )

    resp = client.post(
        "/register",
        data={"csrf_token": "csrf-1", "email": "Alice@Example.com", "password": "x" * 12},
    )

    assert resp.status_code == 302
    assert len(mailer.sent) == 1
    to, _subject, body = mailer.sent[0]
    assert to == "alice@example.com"
    assert "/verify-email?token=verify-token" in body
    with client.session_transaction() as sess:
        assert sess.get("pending_verification_email") == "alice@example.com"
        assert "pending_verification_token" not in sess


def test_webapp_register_survives_send_failure_and_offers_resend(monkeypatch) -> None:
    client = _auth_client(monkeypatch)
    created = []
    monkeypatch.setattr(
        webapp, "_verification_mailer", lambda: _RecordingMailer(fail=True), raising=False
    )
    monkeypatch.setattr(
        webapp,
        "register_local_user",
        lambda email, password, display_name=None: created.append(email) or _Registration(),
    )

    resp = client.post(
        "/register",
        data={"csrf_token": "csrf-1", "email": "alice@example.com", "password": "x" * 12},
        follow_redirects=True,
    )

    assert created, "the account itself must still be created"
    body = resp.get_data(as_text=True).lower()
    assert "could not be sent" in body
    assert "resend" in body


def test_webapp_resend_verification_sends_a_fresh_token(monkeypatch) -> None:
    client = _auth_client(monkeypatch)
    mailer = _RecordingMailer()
    monkeypatch.setattr(webapp, "_verification_mailer", lambda: mailer, raising=False)
    monkeypatch.setattr(
        webapp, "resend_local_verification", lambda email: "fresh-token", raising=False
    )

    resp = client.post(
        "/resend-verification",
        data={"csrf_token": "csrf-1", "email": "alice@example.com"},
    )

    assert resp.status_code == 200
    assert "verification link has been sent" in resp.get_data(as_text=True).lower()
    assert len(mailer.sent) == 1
    assert "/verify-email?token=fresh-token" in mailer.sent[0][2]


def test_webapp_resend_verification_is_indistinguishable_for_unknown_email(monkeypatch) -> None:
    """Anti-enumeration: unknown/verified addresses get the identical page."""
    client = _auth_client(monkeypatch)
    mailer = _RecordingMailer()
    monkeypatch.setattr(webapp, "_verification_mailer", lambda: mailer, raising=False)
    monkeypatch.setattr(webapp, "resend_local_verification", lambda email: None, raising=False)

    resp = client.post(
        "/resend-verification",
        data={"csrf_token": "csrf-1", "email": "nobody@example.com"},
    )

    assert resp.status_code == 200
    assert "verification link has been sent" in resp.get_data(as_text=True).lower()
    assert mailer.sent == []


def test_webapp_login_is_rate_limited(monkeypatch) -> None:
    """Regression for the brute-force MAJOR: /login had no throttle at all."""
    from webapp.rate_limit import FixedWindowRateLimiter

    client = _auth_client(monkeypatch)
    monkeypatch.setenv("DOCRAY_WEBAPP_LOGIN_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("DOCRAY_WEBAPP_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "3600")
    monkeypatch.setattr(webapp, "_RATE_LIMITER", FixedWindowRateLimiter())
    monkeypatch.setattr(
        webapp,
        "complete_local_login",
        lambda email, password: (_ for _ in ()).throw(webapp.InvalidCredentials("nope")),
    )

    first = client.post(
        "/login", data={"csrf_token": "csrf-1", "email": "a@example.com", "password": "x" * 12}
    )
    second = client.post(
        "/login", data={"csrf_token": "csrf-1", "email": "a@example.com", "password": "x" * 12}
    )

    assert first.status_code == 401
    assert second.status_code == 429


def test_webapp_register_is_rate_limited(monkeypatch) -> None:
    from webapp.rate_limit import FixedWindowRateLimiter

    client = _auth_client(monkeypatch)
    monkeypatch.setenv("DOCRAY_WEBAPP_REGISTER_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("DOCRAY_WEBAPP_REGISTER_RATE_LIMIT_WINDOW_SECONDS", "3600")
    monkeypatch.setattr(webapp, "_RATE_LIMITER", FixedWindowRateLimiter())
    monkeypatch.setattr(webapp, "_verification_mailer", lambda: _RecordingMailer())
    monkeypatch.setattr(
        webapp, "register_local_user", lambda email, password, display_name=None: _Registration()
    )

    first = client.post(
        "/register",
        data={"csrf_token": "csrf-1", "email": "a@example.com", "password": "x" * 12},
    )
    second = client.post(
        "/register",
        data={"csrf_token": "csrf-1", "email": "b@example.com", "password": "x" * 12},
    )

    assert first.status_code == 302
    assert second.status_code == 429


def test_webapp_resend_verification_is_rate_limited(monkeypatch) -> None:
    from webapp.rate_limit import FixedWindowRateLimiter

    client = _auth_client(monkeypatch)
    monkeypatch.setenv("DOCRAY_WEBAPP_RESEND_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("DOCRAY_WEBAPP_RESEND_RATE_LIMIT_WINDOW_SECONDS", "3600")
    monkeypatch.setattr(webapp, "_RATE_LIMITER", FixedWindowRateLimiter())
    monkeypatch.setattr(webapp, "_verification_mailer", lambda: _RecordingMailer(), raising=False)
    monkeypatch.setattr(webapp, "resend_local_verification", lambda email: None, raising=False)

    first = client.post(
        "/resend-verification", data={"csrf_token": "csrf-1", "email": "a@example.com"}
    )
    second = client.post(
        "/resend-verification", data={"csrf_token": "csrf-1", "email": "a@example.com"}
    )

    assert first.status_code == 200
    assert second.status_code == 429


def test_webapp_api_headers_forward_the_session_principal(monkeypatch) -> None:
    """Regression for the authorization-inversion MAJOR: proxied API calls
    must act as the logged-in user (X-DocRay-Act-As), not as the webapp's
    admin-grade service token alone."""
    webapp.auth_sessions.reset()
    principal_id = "99999999-9999-9999-9999-999999999999"
    sid = webapp.auth_sessions.create(principal_id=principal_id, subject="local:a@example.com")

    from webapp.graphql_client import api_auth_headers

    with webapp.app.test_request_context("/"):
        from flask import session

        session["_auth_sid"] = sid
        headers = api_auth_headers()

    assert headers.get("X-DocRay-Act-As") == principal_id


def test_webapp_api_headers_omit_act_as_without_a_session(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    from webapp.graphql_client import api_auth_headers

    with webapp.app.test_request_context("/"):
        headers = api_auth_headers()

    assert "X-DocRay-Act-As" not in headers


def test_webapp_header_shows_login_and_register_when_anonymous(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setenv("DOCRAY_AUTH_REGISTRATION_ENABLED", "true")

    resp = webapp.app.test_client().get("/login")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/login"' in body
    assert 'href="/register"' in body
    assert 'href="/profile"' not in body
    assert 'action="/logout"' not in body


def test_webapp_header_shows_profile_and_logout_when_authenticated(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    sid = webapp.auth_sessions.create(
        principal_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
        subject="local:alice@example.com",
    )
    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_auth_sid"] = sid

    resp = client.get("/login")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'href="/profile"' in body
    assert 'action="/logout"' in body
    assert 'href="/login"' not in body


def test_webapp_profile_page_shows_current_session(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    sid = webapp.auth_sessions.create(
        principal_id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
        subject="local:alice@example.com",
    )
    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_auth_sid"] = sid

    resp = client.get("/profile")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "local:alice@example.com" in body
    assert "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee" in body


def test_webapp_requires_auth_for_corpus_when_local_auth_enabled(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setenv("DOCRAY_PRINCIPAL_ID", "ffffffff-ffff-ffff-ffff-ffffffffffff")
    monkeypatch.setattr(webapp, "q", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(webapp, "q1", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError))

    # The corpus now lives at /corpus and is gated; the root landing is public.
    resp = webapp.app.test_client().get("/corpus")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/login")


def test_webapp_landing_is_public_when_local_auth_enabled(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setenv("DOCRAY_PRINCIPAL_ID", "ffffffff-ffff-ffff-ffff-ffffffffffff")

    # The marketing landing must render without a session (no auth redirect).
    resp = webapp.app.test_client().get("/")

    assert resp.status_code == 200
    assert "auditable research corpus" in resp.get_data(as_text=True)


def test_webapp_hides_app_nav_for_anonymous_when_auth_required(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")

    body = webapp.app.test_client().get("/").get_data(as_text=True)

    # App destinations are dead-ends for an anonymous visitor on a gated
    # instance — the top bar must not advertise them.
    assert 'nav-group nav-reading' not in body
    assert 'nav-group nav-admin' not in body
    # The account entry point is still offered.
    assert 'href="/login"' in body


def test_webapp_uses_no_env_principal_fallback_when_auth_required(monkeypatch) -> None:
    webapp.auth_sessions.reset()
    monkeypatch.setenv("DOCRAY_AUTH_LOCAL_ENABLED", "true")
    monkeypatch.setattr(webapp, "PRINCIPAL_ID", "ffffffff-ffff-ffff-ffff-ffffffffffff")

    with webapp.app.test_request_context("/"):
        assert webapp._request_principal_id() is None
