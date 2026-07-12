"""Webapp request-id forwarding tests (BL-117)."""

from __future__ import annotations

import io
import re

import pytest

from webapp import app as webapp


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(webapp, "q", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(webapp, "q1", lambda *_args, **_kwargs: None)
    webapp.app.config.update(TESTING=True)
    webapp.reset_rate_limits()
    return webapp.app.test_client()


def _csrf_token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf_token hidden field found in rendered form"
    return match.group(1)


class _FakeUploadResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"data": {"ingestDocument": {"id": "job-request-id"}}}


def test_upload_forwards_incoming_request_id_to_graphql_api(client, monkeypatch):
    import httpx

    captured = {}

    def fake_post(*_args, **kwargs):
        captured["headers"] = kwargs["headers"]
        return _FakeUploadResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    token = _csrf_token_from(client.get("/upload").get_data(as_text=True))
    response = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "file": (io.BytesIO(b"hello"), "request-id.txt"),
        },
        headers={"X-Request-ID": "req-browser-123"},
    )

    assert response.status_code == 302
    assert captured["headers"]["X-Request-ID"] == "req-browser-123"


def test_upload_generates_request_id_when_missing(client, monkeypatch):
    import httpx

    captured = {}

    def fake_post(*_args, **kwargs):
        captured["headers"] = kwargs["headers"]
        return _FakeUploadResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    token = _csrf_token_from(client.get("/upload").get_data(as_text=True))
    response = client.post(
        "/upload",
        data={
            "csrf_token": token,
            "file": (io.BytesIO(b"hello"), "generated-request-id.txt"),
        },
    )

    assert response.status_code == 302
    assert captured["headers"]["X-Request-ID"].startswith("req-")
