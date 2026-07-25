"""Regression cover: the webapp forwards the logged-in user's credential to the API.

Document page images and original downloads are proxied through the API, which
enforces row-level security against the *caller's* principal. In the production
auth posture (OIDC, ``DOCRAY_API_OIDC_ALLOW_STATIC_TOKEN_FALLBACK=false``) the
webapp must present the USER's own verified bearer token, or the API sees an
anonymous caller, ``docray_readable_documents()`` returns nothing, and every
image/download 404s (the "broken images on the web" failure).

These lock the header-building contract so that regression cannot return:
a logged-in OIDC user's token is forwarded; anonymous callers send no auth; and
the legacy static-token + act-as path is used only as a fallback.
"""

from __future__ import annotations

import re

import pytest
from flask import session

import webapp.auth_web as auth_web
from webapp import app as webapp
from webapp import graphql_client
from webapp.auth_session import auth_sessions


def _login(*, bearer: str | None, principal_id: str = "p-1") -> str:
    auth_sessions.reset()
    return auth_sessions.create(
        principal_id=principal_id, subject="oidc:alice", bearer_token=bearer
    )


def test_web_auth_session_persists_bearer_token():
    auth_sessions.reset()
    sid = auth_sessions.create(principal_id="p", subject="s", bearer_token="tok")
    record = auth_sessions.get(sid)
    assert record is not None and record.bearer_token == "tok"


def test_api_auth_headers_forwards_logged_in_user_bearer_token(monkeypatch):
    monkeypatch.setattr(graphql_client, "API_TOKEN", "service-token")  # must NOT win
    sid = _login(bearer="user-jwt-123", principal_id="p-42")
    with webapp.app.test_request_context("/"):
        session["_auth_sid"] = sid
        headers = graphql_client.api_auth_headers()
    assert headers["Authorization"] == "Bearer user-jwt-123"
    # The token identifies the user; no act-as impersonation is added.
    assert "X-DocRay-Act-As" not in headers


def test_api_auth_headers_anonymous_sends_no_authorization(monkeypatch):
    monkeypatch.setattr(graphql_client, "API_TOKEN", None)
    auth_sessions.reset()
    with webapp.app.test_request_context("/"):
        headers = graphql_client.api_auth_headers()
    assert "Authorization" not in headers
    assert "X-DocRay-Act-As" not in headers


def test_api_auth_headers_falls_back_to_service_token_and_act_as(monkeypatch):
    # A session without a forwardable bearer (e.g. SAML) uses the legacy path.
    monkeypatch.setattr(graphql_client, "API_TOKEN", "service-token")
    sid = _login(bearer=None, principal_id="p-9")
    with webapp.app.test_request_context("/"):
        session["_auth_sid"] = sid
        headers = graphql_client.api_auth_headers()
    assert headers["Authorization"] == "Bearer service-token"
    assert headers["X-DocRay-Act-As"] == "p-9"


def test_semantic_search_graphql_read_forwards_logged_in_bearer(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"semanticSearchSentences": []}}

    def post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Response()

    monkeypatch.setattr("httpx.post", post)
    sid = _login(bearer="reader-jwt", principal_id="p-reader")
    with webapp.app.test_request_context("/search?mode=semantic&q=ending"):
        session["_auth_sid"] = sid
        result = graphql_client.gql(
            "query($query: String!) { semanticSearchSentences(query: $query) { ordinal } }",
            {"query": "ending"},
        )

    assert result == {"semanticSearchSentences": []}
    assert captured["headers"]["Authorization"] == "Bearer reader-jwt"
    assert "X-DocRay-Act-As" not in captured["headers"]
    assert captured["json"]["variables"] == {"query": "ending"}


def test_visual_gallery_graphql_read_forwards_logged_in_bearer(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "documentImages": [],
                    "imageClassSummary": [],
                    "visualCaptions": [],
                }
            }

    def post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Response()

    monkeypatch.setattr("httpx.post", post)
    sid = _login(bearer="visual-reader-jwt", principal_id="p-visual-reader")
    with webapp.app.test_request_context("/doc/example?view=images"):
        session["_auth_sid"] = sid
        graphql_client.gql(
            """query($id: ID!) {
              documentImages(documentId: $id) { id }
              imageClassSummary(documentId: $id) { imageClass count }
              visualCaptions(documentId: $id) { id caption }
            }""",
            {"id": "doc-1"},
        )

    assert captured["headers"]["Authorization"] == "Bearer visual-reader-jwt"
    assert "X-DocRay-Act-As" not in captured["headers"]
    assert "visualCaptions" in captured["json"]["query"]


def test_briefing_read_forwards_logged_in_bearer(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "briefingPack": {
                        "topic": "gravity",
                        "retrieval": "keyword",
                        "sources": [],
                        "keyPassages": [],
                        "entities": [],
                        "coverage": {"documents": 0, "passages": 0},
                        "gaps": ["No documents."],
                        "guidance": "Verify citations.",
                    }
                }
            }

    def post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Response()

    monkeypatch.setattr("httpx.post", post)
    monkeypatch.setattr(webapp, "gql", graphql_client.gql)
    sid = _login(bearer="briefing-reader-jwt", principal_id="p-briefing")
    client = webapp.app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["_auth_sid"] = sid

    response = client.get("/briefing?topic=gravity")

    assert response.status_code == 200
    assert captured["headers"]["Authorization"] == "Bearer briefing-reader-jwt"
    assert "X-DocRay-Act-As" not in captured["headers"]
    assert "briefingPack" in captured["json"]["query"]


def test_review_decision_post_forwards_logged_in_bearer(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"decideVisualCaptions": 1}}

    def post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Response()

    monkeypatch.setattr("httpx.post", post)
    monkeypatch.setattr(webapp, "q", lambda *_a, **_k: [])
    monkeypatch.setattr(webapp, "q1", lambda *_a, **_k: {})
    monkeypatch.setattr(webapp, "gql", graphql_client.gql)
    sid = _login(bearer="curator-jwt", principal_id="p-curator")
    client = webapp.app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["_auth_sid"] = sid
    page = client.get("/review?lane=visual").get_data(as_text=True)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert csrf is not None

    response = client.post(
        "/review",
        data={
            "csrf_token": csrf.group(1),
            "lane": "visual",
            "kind": "visual",
            "action": "approve",
            "item_id": "visual-1",
        },
    )

    assert response.status_code == 302
    assert captured["headers"]["Authorization"] == "Bearer curator-jwt"
    assert "decideVisualCaptions" in captured["json"]["query"]


def test_lens_write_post_forwards_logged_in_bearer(monkeypatch):
    collection_id = "55555555-5555-5555-5555-555555555555"
    captured = {}

    class _Response:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    def post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        if "promoteCollectionToLens" in json["query"]:
            return _Response(
                {
                    "data": {
                        "promoteCollectionToLens": {
                            "id": collection_id,
                            "isScope": True,
                            "parentLensId": None,
                        }
                    }
                }
            )
        return _Response({"data": {"lenses": []}})

    monkeypatch.setattr("httpx.post", post)
    monkeypatch.setattr(webapp, "q", lambda *_a, **_k: [])
    monkeypatch.setattr(
        webapp,
        "q1",
        lambda *_a, **_k: {
            "id": collection_id,
            "name": "reading-list",
            "description": "",
            "kind": "manual",
            "rule": {},
            "is_scope": False,
            "parent_lens_id": None,
        },
    )
    monkeypatch.setattr(webapp, "gql", graphql_client.gql)
    sid = _login(bearer="lens-curator-jwt", principal_id="p-lens-curator")
    client = webapp.app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["_auth_sid"] = sid
    page = client.get(f"/collections/{collection_id}").get_data(as_text=True)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert csrf is not None

    response = client.post(
        f"/collections/{collection_id}/lens",
        data={"csrf_token": csrf.group(1), "action": "promote", "parent_lens_id": ""},
    )

    assert response.status_code == 302
    assert captured["headers"]["Authorization"] == "Bearer lens-curator-jwt"
    assert "promoteCollectionToLens" in captured["json"]["query"]


def test_complete_oidc_login_retains_verified_id_token(monkeypatch):
    monkeypatch.setattr(
        auth_web, "oidc_claims_from_code", lambda **_k: ({"sub": "alice"}, "id-token-xyz")
    )
    monkeypatch.setattr(auth_web, "_resolve_web_principal", lambda _subject: "p-oidc")
    principal = auth_web.complete_oidc_login("code", "https://cb")
    assert principal.principal_id == "p-oidc"
    assert principal.bearer_token == "id-token-xyz"


def test_complete_saml_login_has_no_forwardable_bearer(monkeypatch):
    class _Assertion:
        external_subject = "saml:bob"

    monkeypatch.setattr(auth_web, "saml_assertion_from_response", lambda _resp: _Assertion())
    monkeypatch.setattr(auth_web, "_resolve_web_principal", lambda _subject: "p-saml")
    principal = auth_web.complete_saml_login("response")
    assert principal.principal_id == "p-saml"
    assert principal.bearer_token is None


def test_db_role_guard_rejects_client_role_under_browser_auth():
    """The webapp must refuse to boot as docray_client when browser auth is on.

    Only docray_api resolves the per-session app.principal_id, so docray_client +
    browser auth = a silently empty corpus. The guard turns that into a loud
    boot failure. Regression for the DOCRAY_DB_ROLE drift that hid all documents.
    """
    from webapp.app import _assert_db_role_resolves_principals

    with pytest.raises(RuntimeError, match="docray_api"):
        _assert_db_role_resolves_principals("docray_client", browser_auth_required=True)


def test_db_role_guard_allows_api_role_and_no_auth():
    from webapp.app import _assert_db_role_resolves_principals

    # docray_api under browser auth is the supported per-user config.
    _assert_db_role_resolves_principals("docray_api", browser_auth_required=True)
    # Without browser auth, docray_client (single-principal/dev) is fine.
    _assert_db_role_resolves_principals("docray_client", browser_auth_required=False)
