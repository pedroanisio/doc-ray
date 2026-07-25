"""The webapp half of ``DOCRAY_BACKUP_ENABLED``.

Routes and navigation are gated by the same flag the API enforces, so a
switched-off deployment neither offers a Backups link nor serves the pages
behind it. The API-side enforcement points are covered in
``src/doc_ray/tests/test_backup_toggle.py``.

The routes stay *registered* in both positions — ``url_for('backups')`` is
referenced from the shared base template, so unregistering them would turn every
page into a ``BuildError``. The flag decides what they answer, not whether they
exist.
"""

from __future__ import annotations

import pytest

from webapp import admin_web
from webapp import app as webapp


@pytest.fixture
def client(monkeypatch):
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


@pytest.fixture
def backup_off(monkeypatch):
    monkeypatch.delenv("DOCRAY_BACKUP_ENABLED", raising=False)


@pytest.fixture
def backup_on(monkeypatch):
    monkeypatch.setenv("DOCRAY_BACKUP_ENABLED", "true")


def _post(client, path):
    """POST *path* past the CSRF pre-hook, so the backup gate is what answers.

    Without a valid token every POST is refused with 400 by ``require_csrf_token``
    before routing — which would make a "disabled" assertion pass for the wrong
    reason.
    """
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "test-token"
    return client.post(path, data={"csrf_token": "test-token"})


# --- the flag helper --------------------------------------------------------


def test_webapp_reads_the_same_flag_as_the_api(monkeypatch):
    """The webapp's own parse must agree with Config._env_bool, spelling for spelling."""
    from doc_ray.config import Config

    for raw in ("1", "true", "TRUE", "yes", "on", "0", "false", "no", "off", "", "maybe"):
        monkeypatch.setenv("DOCRAY_BACKUP_ENABLED", raw)
        assert admin_web.backup_enabled() is Config.from_env().backup_enabled, raw


def test_backup_disabled_by_default(backup_off):
    assert admin_web.backup_enabled() is False


# --- routes -----------------------------------------------------------------


@pytest.mark.parametrize("path", ["/backups", "/backups/01ABC/download"])
def test_backup_get_routes_404_when_disabled(client, backup_off, path):
    """404, not 403: a switched-off subsystem should look absent, not forbidden."""
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/backups", "/backups/01ABC/restore"])
def test_backup_post_routes_404_when_disabled(client, backup_off, path):
    """The write paths are gated too, checked with a valid CSRF token."""
    assert _post(client, path).status_code == 404


def test_disabled_routes_never_reach_the_database_or_the_api(client, backup_off, monkeypatch):
    """The gate runs first — a disabled route must not query or call out."""

    def _explode(*_a, **_k):  # pragma: no cover - asserts it is never called
        raise AssertionError("a disabled backup route must not touch the backend")

    monkeypatch.setattr(webapp, "q", _explode)
    monkeypatch.setattr(webapp, "gql", _explode)
    assert client.get("/backups").status_code == 404
    assert _post(client, "/backups").status_code == 404


def test_backups_page_renders_when_enabled(client, backup_on, monkeypatch):
    """With the switch on the same route serves the catalogue."""
    monkeypatch.setattr(webapp, "q", lambda *_a, **_k: [])
    response = client.get("/backups")
    assert response.status_code == 200
    assert b"Backup" in response.data


# --- navigation -------------------------------------------------------------


def test_nav_hides_the_backups_link_when_disabled(client, backup_off, monkeypatch):
    """No link may point at a route that answers 404."""
    monkeypatch.setattr(webapp, "q", lambda *_a, **_k: [])
    body = client.get("/help").data
    assert b'href="/backups"' not in body


def test_nav_shows_the_backups_link_when_enabled(client, backup_on, monkeypatch):
    monkeypatch.setattr(webapp, "q", lambda *_a, **_k: [])
    body = client.get("/help").data
    assert b'href="/backups"' in body
