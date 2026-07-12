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
