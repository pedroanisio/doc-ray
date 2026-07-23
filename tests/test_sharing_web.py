"""Webapp sharing routes: list, grant, revoke go through the GraphQL API.

The browser never touches document_acl; every path forwards to the API under the
signed-in principal, so authorization is the API's (tested in
test_mutations_sharing.py). These tests pin the webapp glue: the right mutation
is sent, and the API's own error message survives to the user.
"""

from __future__ import annotations

import webapp.app as webapp

_DOC = "11111111-1111-1111-1111-111111111111"


def _client(monkeypatch, *, gql):
    monkeypatch.setattr(webapp, "q1", lambda sql, params=(): {"id": _DOC, "title": "Doc"})
    monkeypatch.setattr(webapp, "gql", gql)
    webapp.app.config.update(TESTING=True)
    client = webapp.app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "t"
    return client


def test_sharing_page_lists_current_grants(monkeypatch):
    def gql(query, variables=None):
        assert "documentShares" in query
        return {
            "documentShares": [
                {
                    "principalId": "p",
                    "email": "a@x.com",
                    "permission": "read",
                    "grantedAt": "2026-07-23T00:00:00Z",
                }
            ]
        }

    client = _client(monkeypatch, gql=gql)
    html = client.get(f"/doc/{_DOC}/sharing").get_data(as_text=True)
    assert "a@x.com" in html
    assert "read" in html


def test_grant_sends_the_share_mutation_and_redirects(monkeypatch):
    calls = {}

    def gql(query, variables=None):
        if "shareDocument" in query:
            calls["share"] = variables
            return {"shareDocument": {"permission": "annotate"}}
        return {"documentShares": []}

    client = _client(monkeypatch, gql=gql)
    resp = client.post(
        f"/doc/{_DOC}/sharing/grant",
        data={"csrf_token": "t", "email": "b@x.com", "permission": "annotate"},
    )
    assert resp.status_code == 302
    assert calls["share"] == {"id": _DOC, "email": "b@x.com", "permission": "annotate"}
    assert "shared=b@x.com" in resp.headers["Location"]


def test_grant_surfaces_the_api_error_message(monkeypatch):
    def gql(query, variables=None):
        raise RuntimeError("cannot share document")

    client = _client(monkeypatch, gql=gql)
    resp = client.post(f"/doc/{_DOC}/sharing/grant", data={"csrf_token": "t", "email": "b@x.com"})
    assert resp.status_code == 302
    assert "error=cannot" in resp.headers["Location"]


def test_revoke_sends_the_revoke_mutation(monkeypatch):
    calls = {}

    def gql(query, variables=None):
        assert "revokeDocumentShare" in query
        calls["revoke"] = variables
        return {"revokeDocumentShare": 1}

    client = _client(monkeypatch, gql=gql)
    resp = client.post(f"/doc/{_DOC}/sharing/revoke", data={"csrf_token": "t", "email": "b@x.com"})
    assert resp.status_code == 302
    assert calls["revoke"] == {"id": _DOC, "email": "b@x.com"}
    assert "revoked=b@x.com" in resp.headers["Location"]


def test_grant_without_email_is_rejected_before_the_api(monkeypatch):
    def gql(query, variables=None):
        raise AssertionError("must not call the API without an email")

    client = _client(monkeypatch, gql=gql)
    resp = client.post(f"/doc/{_DOC}/sharing/grant", data={"csrf_token": "t", "email": ""})
    assert resp.status_code == 302
    assert "error=enter+an+email" in resp.headers["Location"]
