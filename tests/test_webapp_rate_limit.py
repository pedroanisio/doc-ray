"""Rate-limit regression tests for expensive webapp routes (BL-105)."""

from __future__ import annotations

import re

import pytest

from webapp import app as webapp
from webapp.rate_limit import FixedWindowRateLimiter, RateLimitRule

DOC_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client(monkeypatch):
    def fake_q(sql, params=()):
        if "FROM sentences s" in " ".join(sql.split()) and "ILIKE" in sql:
            return [
                {
                    "doc_id": DOC_ID,
                    "title": "orwell1984",
                    "ordinal": 3,
                    "text": "Winston loved Big Brother.",
                }
            ]
        return []

    monkeypatch.setattr(webapp, "q", fake_q)
    monkeypatch.setattr(webapp, "q1", lambda *_args, **_kwargs: None)
    webapp.app.config.update(TESTING=True)
    webapp.reset_rate_limits()
    return webapp.app.test_client()


def _csrf_token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf_token hidden field found in rendered form"
    return match.group(1)


def test_fixed_window_rate_limiter_limits_each_client_independently():
    now = 1000.0
    limiter = FixedWindowRateLimiter(clock=lambda: now)
    rule = RateLimitRule(requests=2, window_seconds=60)

    first = limiter.check("search", "203.0.113.10", rule)
    second = limiter.check("search", "203.0.113.10", rule)
    third = limiter.check("search", "203.0.113.10", rule)
    other_client = limiter.check("search", "203.0.113.11", rule)

    assert first.allowed is True
    assert second.allowed is True
    assert third.allowed is False
    assert third.retry_after_seconds == 60
    assert other_client.allowed is True


def test_fixed_window_rate_limiter_resets_after_window():
    now = 1000.0
    limiter = FixedWindowRateLimiter(clock=lambda: now)
    rule = RateLimitRule(requests=1, window_seconds=60)

    assert limiter.check("upload", "203.0.113.10", rule).allowed is True
    assert limiter.check("upload", "203.0.113.10", rule).allowed is False

    now = 1060.1

    assert limiter.check("upload", "203.0.113.10", rule).allowed is True


def test_search_returns_clean_429_when_rate_limit_is_exceeded(client, monkeypatch):
    monkeypatch.setenv("DOCRAY_WEBAPP_SEARCH_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("DOCRAY_WEBAPP_SEARCH_RATE_LIMIT_WINDOW_SECONDS", "60")
    webapp.reset_rate_limits()

    first = client.get("/search?q=Winston", environ_base={"REMOTE_ADDR": "203.0.113.10"})
    second = client.get("/search?q=Winston", environ_base={"REMOTE_ADDR": "203.0.113.10"})
    other_client = client.get("/search?q=Winston", environ_base={"REMOTE_ADDR": "203.0.113.11"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert "Too many requests" in second.get_data(as_text=True)
    assert second.headers["Retry-After"] == "60"
    assert other_client.status_code == 200


def test_upload_returns_clean_429_when_rate_limit_is_exceeded(client, monkeypatch):
    monkeypatch.setenv("DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("DOCRAY_WEBAPP_UPLOAD_RATE_LIMIT_WINDOW_SECONDS", "60")
    webapp.reset_rate_limits()

    token = _csrf_token_from(client.get("/upload").get_data(as_text=True))
    first = client.post(
        "/upload",
        data={"csrf_token": token},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    second = client.post(
        "/upload",
        data={"csrf_token": token},
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )

    assert first.status_code == 302
    assert second.status_code == 429
    assert "Too many requests" in second.get_data(as_text=True)
    assert second.headers["Retry-After"] == "60"
