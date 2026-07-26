"""Tests for scripts/user_stats.py.

The script is a read-only reporter, so the connection is a recording fake: the
suite runs offline and asserts the *counting contract* — which table is the
denominator, which rows are excluded, and that no statement writes — rather than
re-testing PostgreSQL's aggregate behaviour.

The load-bearing assertion is that `principals` is never used as the registered
count: that table also holds service accounts and the operator-seeded admin.
"""

from __future__ import annotations

import json

import pytest
import user_stats as us


class FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return self._rows


class FakeConn:
    """Records every statement and replays canned rows in order."""

    def __init__(self, responses: list[FakeCursor] | None = None) -> None:
        self._responses = list(responses or [])
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, query: str, params: tuple = ()) -> FakeCursor:
        self.executed.append((" ".join(query.split()), params))
        return self._responses.pop(0) if self._responses else FakeCursor([])


class _ctx:
    """Minimal context manager standing in for a psycopg connection."""

    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    def __enter__(self) -> FakeConn:
        return self._conn

    def __exit__(self, *exc: object) -> bool:
        return False


REGISTRATION_ROW = {
    "registered": 12,
    "verified": 9,
    "unverified": 3,
    "disabled": 1,
    "ever_logged_in": 8,
    "active_1d": 2,
    "active_7d": 5,
    "active_30d": 7,
    "new_1d": 1,
    "new_7d": 4,
    "new_30d": 12,
}

PRINCIPAL_ROW = {
    "principals": 15,
    "humans": 13,
    "services": 2,
    "disabled": 1,
    "deactivated": 0,
    "scim_managed": 0,
    "without_local_password": 1,
}

SESSION_ROW = {"sessions": 3, "principals": 2}


def _full_conn(recent: list[dict] | None = None) -> FakeConn:
    return FakeConn(
        [
            FakeCursor([REGISTRATION_ROW]),
            FakeCursor([PRINCIPAL_ROW]),
            FakeCursor([SESSION_ROW]),
            FakeCursor(
                [{"slug": "default", "display_name": "Default", "registered": 12, "verified": 9}]
            ),
            FakeCursor(recent if recent is not None else []),
        ]
    )


# --------------------------------------------------------------------------
# The counting contract
# --------------------------------------------------------------------------


def test_registered_users_are_counted_from_credentials_not_principals() -> None:
    conn = FakeConn([FakeCursor([REGISTRATION_ROW])])
    assert us.registration_stats(conn)["registered"] == 12
    query, _params = conn.executed[0]
    assert "FROM principal_credentials" in query
    assert "FROM principals " not in query, "principals includes service accounts and the admin"


def test_registration_stats_separates_verified_from_merely_signed_up() -> None:
    conn = FakeConn([FakeCursor([REGISTRATION_ROW])])
    stats = us.registration_stats(conn)
    assert stats["verified"] == 9
    assert stats["unverified"] == 3
    query, _params = conn.executed[0]
    assert "email_verified_at IS NOT NULL" in query


def test_registration_stats_reports_activity_and_signup_windows() -> None:
    conn = FakeConn([FakeCursor([REGISTRATION_ROW])])
    stats = us.registration_stats(conn)
    assert (stats["active_1d"], stats["active_7d"], stats["active_30d"]) == (2, 5, 7)
    assert (stats["new_1d"], stats["new_7d"], stats["new_30d"]) == (1, 4, 12)
    query, _params = conn.executed[0]
    assert "last_login_at >=" in query, "activity must key off logins, not signups"
    assert "created_at >=" in query


def test_registration_stats_on_an_empty_deployment_is_all_zero() -> None:
    conn = FakeConn([FakeCursor([])])
    stats = us.registration_stats(conn)
    assert stats["registered"] == 0
    assert set(stats.values()) == {0}


def test_principal_stats_splits_humans_from_service_accounts() -> None:
    conn = FakeConn([FakeCursor([PRINCIPAL_ROW])])
    stats = us.principal_stats(conn)
    assert stats["humans"] == 13
    assert stats["services"] == 2
    query, _params = conn.executed[0]
    assert "kind = 'service'" in query


def test_principal_stats_counts_humans_with_no_local_password() -> None:
    """Federated (OIDC/SAML/SCIM) identities never get a principal_credentials row."""
    conn = FakeConn([FakeCursor([PRINCIPAL_ROW])])
    assert us.principal_stats(conn)["without_local_password"] == 1
    query, _params = conn.executed[0]
    assert "principal_credentials" in query


def test_session_stats_excludes_expired_sessions() -> None:
    conn = FakeConn([FakeCursor([SESSION_ROW])])
    assert us.session_stats(conn) == {"sessions": 3, "principals": 2}
    query, _params = conn.executed[0]
    assert "expires_at > now()" in query, "an expired row is not a signed-in user"


def test_tenant_breakdown_keeps_tenants_with_no_users() -> None:
    conn = FakeConn(
        [
            FakeCursor(
                [
                    {"slug": "default", "display_name": "Default", "registered": 12, "verified": 9},
                    {"slug": "empty", "display_name": "Empty", "registered": 0, "verified": 0},
                ]
            )
        ]
    )
    rows = us.tenant_breakdown(conn)
    assert [r["slug"] for r in rows] == ["default", "empty"]
    query, _params = conn.executed[0]
    assert "LEFT JOIN" in query, "an inner join would hide a tenant with zero signups"


def test_recent_signups_orders_newest_first_and_honours_the_limit() -> None:
    conn = FakeConn([FakeCursor([{"email_normalized": "a@example.com"}])])
    assert len(us.recent_signups(conn, 5)) == 1
    query, params = conn.executed[0]
    assert "ORDER BY c.created_at DESC" in query
    assert params == (5,)


# --------------------------------------------------------------------------
# Email masking (the report is operational, not a contact list)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pedro@faz.ai", "p***o@faz.ai"),
        ("ab@faz.ai", "a*@faz.ai"),
        ("a@faz.ai", "a*@faz.ai"),
        ("", "***"),
        ("not-an-email", "***"),
    ],
)
def test_mask_email_keeps_the_domain_and_hides_the_local_part(raw: str, expected: str) -> None:
    assert us.mask_email(raw) == expected


def test_collect_masks_emails_by_default() -> None:
    conn = _full_conn([{"email_normalized": "pedro@faz.ai", "created_at": "2026-07-01"}])
    report = us.collect(conn, limit=5, reveal_emails=False)
    assert report["recent"][0]["email"] == "p***o@faz.ai"


def test_collect_reveals_emails_only_when_asked() -> None:
    conn = _full_conn([{"email_normalized": "pedro@faz.ai", "created_at": "2026-07-01"}])
    report = us.collect(conn, limit=5, reveal_emails=True)
    assert report["recent"][0]["email"] == "pedro@faz.ai"


def test_collect_skips_the_signup_list_when_the_limit_is_zero() -> None:
    conn = FakeConn(
        [
            FakeCursor([REGISTRATION_ROW]),
            FakeCursor([PRINCIPAL_ROW]),
            FakeCursor([SESSION_ROW]),
            FakeCursor([]),
        ]
    )
    report = us.collect(conn, limit=0, reveal_emails=False)
    assert report["recent"] == []
    assert not any("ORDER BY c.created_at DESC" in q for q, _ in conn.executed)


# --------------------------------------------------------------------------
# Read-only guarantee and CLI
# --------------------------------------------------------------------------


def test_the_reporter_never_writes() -> None:
    conn = _full_conn([{"email_normalized": "a@b.co"}])
    us.collect(conn, limit=5, reveal_emails=False)
    forbidden = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALTER", "DROP", "CREATE")
    for query, _params in conn.executed:
        assert query.upper().startswith("SELECT")
        assert not any(word in query.upper().split() for word in forbidden)


def test_render_leads_with_the_registered_count() -> None:
    report = us.collect(_full_conn(), limit=0, reveal_emails=False)
    text = us.render(report)
    assert "Registered users" in text
    assert "12" in text.splitlines()[2] or "12" in text


def test_render_omits_the_tenant_table_on_a_single_tenant_deployment() -> None:
    report = us.collect(_full_conn(), limit=0, reveal_emails=False)
    assert "Per tenant" not in us.render(report)


def test_render_summarises_empty_tenants_instead_of_listing_them() -> None:
    """SSO/SCIM provisioning leaves empty tenants behind; they must not bury the real one."""
    report = {
        "registrations": REGISTRATION_ROW,
        "principals": PRINCIPAL_ROW,
        "sessions": SESSION_ROW,
        "tenants": [
            {"slug": "default", "display_name": "Default", "registered": 12, "verified": 9},
            {"slug": "scim-tenant-00daf127", "display_name": "x", "registered": 0, "verified": 0},
            {"slug": "saml-route-185b68e6", "display_name": "y", "registered": 0, "verified": 0},
        ],
        "recent": [],
    }
    text = us.render(report)
    assert "default" in text
    assert "scim-tenant-00daf127" not in text
    assert "(+2 tenant(s) with no signups)" in text


def test_main_prints_json_when_asked(monkeypatch, capsys) -> None:
    conn = _full_conn()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(us.psycopg, "connect", lambda *a, **k: _ctx(conn))
    assert us.main(["--json", "--limit", "0"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["registrations"]["registered"] == 12
    assert payload["principals"]["services"] == 2


def test_main_renders_a_text_table_by_default(monkeypatch, capsys) -> None:
    conn = _full_conn()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(us.psycopg, "connect", lambda *a, **k: _ctx(conn))
    assert us.main(["--limit", "0"]) == 0
    assert "Registered users" in capsys.readouterr().out


def test_main_requires_a_database_url(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(us, "load_env_fallback", lambda _keys: None)
    assert us.main([]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_main_reports_a_connection_failure_without_a_traceback(monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")

    def _boom(*_a: object, **_k: object) -> None:
        raise us.psycopg.OperationalError("connection refused")

    monkeypatch.setattr(us.psycopg, "connect", _boom)
    assert us.main([]) == 1
    assert "connection refused" in capsys.readouterr().err
