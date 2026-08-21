"""Intake, the picker widget, the proportion meter, and the inline-style budget.

Four things land here that the rest of the webapp suite does not cover:

  * ``/upload/file`` — the JSON intake that makes a multi-file queue with
    per-file progress possible. Its contract is what the browser depends on, so
    the shape of every reply is pinned, including the failures.
  * the enhanced widgets — dropzone, combobox, row filter — which must all be
    *enhancements*: the markup underneath has to work with no JavaScript, and
    that is a property of the served HTML, so a test can check it.
  * the meter, which draws geometry as SVG attributes rather than as inline
    style, and must never divide by a zero denominator.
  * the inline-style budget, which is what lets the CSP forbid inline style
    everywhere except the one route that cannot avoid it.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from webapp import app as webapp
from webapp.security_headers import CSP, CSP_INLINE_STYLE, csp_for_endpoint

_TEMPLATES = Path(__file__).resolve().parent.parent / "webapp" / "templates"
_STATIC = Path(__file__).resolve().parent.parent / "webapp" / "static"

COLLECTION_ID = "55555555-5555-5555-5555-555555555555"
DOC_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client():
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


def _csrf(client) -> str:
    html = client.get("/upload").get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "the upload form carries no CSRF token"
    return match.group(1)


class _Reply:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# --------------------------------------------------------------------------
# /upload/file — the JSON intake
# --------------------------------------------------------------------------


def test_single_file_intake_answers_with_the_job(client, monkeypatch):
    monkeypatch.setattr(
        "httpx.post", lambda url, **kw: _Reply({"data": {"ingestDocument": {"id": "job-1"}}})
    )
    response = client.post(
        "/upload/file",
        data={"file": (io.BytesIO(b"hello"), "a.txt"), "csrf_token": _csrf(client)},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["jobId"] == "job-1"
    assert body["job"] == "/job/job-1"
    # The name comes back so the browser can match the reply to its own row
    # rather than assuming replies arrive in the order the files were sent.
    assert body["name"] == "a.txt"


def test_intake_reports_the_api_rejection_verbatim(client, monkeypatch):
    monkeypatch.setattr(
        "httpx.post",
        lambda url, **kw: _Reply({"errors": [{"message": "unsupported format .xyz"}]}),
    )
    response = client.post(
        "/upload/file",
        data={"file": (io.BytesIO(b"x"), "b.xyz"), "csrf_token": _csrf(client)},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "unsupported format .xyz"
    assert response.get_json()["name"] == "b.xyz"


def test_intake_distinguishes_an_unreachable_api_from_a_rejected_file(client, monkeypatch):
    """A 400 means "fix the file"; a 502 means "the file was fine". Collapsing
    them would send the operator to edit a document that was never read."""
    import httpx

    def unreachable(url, **kw):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr("httpx.post", unreachable)
    response = client.post(
        "/upload/file",
        data={"file": (io.BytesIO(b"x"), "c.txt"), "csrf_token": _csrf(client)},
        content_type="multipart/form-data",
    )

    assert response.status_code == 502
    assert "unreachable" in response.get_json()["error"]


def test_intake_rejects_a_request_with_no_file(client):
    response = client.post(
        "/upload/file", data={"csrf_token": _csrf(client)}, content_type="multipart/form-data"
    )

    assert response.status_code == 400
    assert "no file" in response.get_json()["error"]


def test_intake_requires_the_session_csrf_token(client):
    """The asynchronous path is a state-changing POST like any other."""
    response = client.post(
        "/upload/file",
        data={"file": (io.BytesIO(b"x"), "d.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400


def test_intake_forwards_the_pipeline_and_extractor_options(client, monkeypatch):
    seen: dict = {}

    def capture(url, **kw):
        seen.update(kw)
        return _Reply({"data": {"ingestDocument": {"id": "job-2"}}})

    monkeypatch.setattr("httpx.post", capture)
    client.post(
        "/upload/file",
        data={
            "file": (io.BytesIO(b"x"), "e.pdf"),
            "csrf_token": _csrf(client),
            "pipeline": "on",
            "patent": "on",
            "jurisdiction": "de",
        },
        content_type="multipart/form-data",
    )

    import json

    variables = json.loads(seen["data"]["operations"])["variables"]
    assert variables["pipeline"] is True
    assert variables["extractor"] == "patent"
    # Upper-cased, because the office code selects an OCR language pack.
    assert variables["jurisdiction"] == "DE"


def test_a_jurisdiction_without_the_patent_reader_is_dropped(client, monkeypatch):
    """An office code recorded against a document the reader never steered would
    claim a provenance that does not exist."""
    seen: dict = {}

    def capture(url, **kw):
        seen.update(kw)
        return _Reply({"data": {"ingestDocument": {"id": "job-3"}}})

    monkeypatch.setattr("httpx.post", capture)
    client.post(
        "/upload/file",
        data={
            "file": (io.BytesIO(b"x"), "f.pdf"),
            "csrf_token": _csrf(client),
            "jurisdiction": "DE",
        },
        content_type="multipart/form-data",
    )

    import json

    variables = json.loads(seen["data"]["operations"])["variables"]
    assert variables["extractor"] is None
    assert variables["jurisdiction"] is None


# --------------------------------------------------------------------------
# The widgets are enhancements, not requirements
# --------------------------------------------------------------------------


def test_the_upload_form_still_posts_without_javascript(client):
    """Drag-and-drop is layered on top of a form that submits on its own."""
    html = client.get("/upload").get_data(as_text=True)

    assert '<form method="post" enctype="multipart/form-data"' in html
    assert 'type="file" name="file"' in html
    assert "data-dropzone" in html
    # No `multiple` in the served markup: the form path posts one `file` part,
    # and the script adds `multiple` only once it is there to handle the rest.
    assert 'name="file" multiple' not in html


def test_the_dropzone_keeps_a_real_focusable_input(client):
    """A div you can only click is not a control. The input stays, covered."""
    html = client.get("/upload").get_data(as_text=True)
    zone = html.split('class="dropzone"')[1].split("</div>")[0]

    assert 'type="file"' in zone
    assert "aria-label=" in zone


def test_upload_progress_uses_a_native_progress_element():
    """`<progress>` is announced and has a value; a styled div has neither."""
    js = (_STATIC / "upload.js").read_text(encoding="utf-8")

    assert 'createElement("progress")' in js


def test_uploads_are_sequential():
    """Each request holds an API worker for a whole ingest, so parallel posts
    make every one slower and make the bars misreport which is moving."""
    js = (_STATIC / "upload.js").read_text(encoding="utf-8")

    assert "Uploads run one at a time" in js
    assert "XMLHttpRequest" in js  # fetch() has no upload progress event


def test_one_title_is_never_applied_to_several_files():
    js = (_STATIC / "upload.js").read_text(encoding="utf-8")

    assert "single && title" in js


@pytest.fixture
def manual_collection(monkeypatch):
    """A manual collection with one addable document, so the picker renders."""

    def fake_q1(sql, params=()):
        if "FROM document_collections WHERE id" in " ".join(sql.split()):
            return {
                "id": COLLECTION_ID,
                "name": "Cold War primary sources",
                "kind": "manual",
                "description": None,
                "is_scope": False,
                "parent_lens_id": None,
                "rule": {},
            }
        return None

    def fake_q(sql, params=()):
        s = " ".join(sql.split())
        # The addable set: every visible document not already a member.
        if "FROM documents d" in s and "NOT EXISTS" in s:
            return [{"id": DOC_ID, "title": "Dune", "doc_type": "pdf"}]
        return []

    monkeypatch.setattr(webapp, "q1", fake_q1)
    monkeypatch.setattr(webapp, "q", fake_q)
    monkeypatch.setattr(webapp, "gql", lambda *a, **k: {"collections": []})
    webapp.app.config.update(TESTING=True)
    return webapp.app.test_client()


def test_the_combobox_options_are_submit_buttons_of_their_own_forms(manual_collection):
    """With JS off the picker is a list of Add buttons, and each still posts.

    This is what makes the widget an enhancement rather than a requirement: the
    combobox script filters and highlights, but the thing it filters is already
    a working list of forms.
    """
    html = manual_collection.get(f"/collections/{COLLECTION_ID}").get_data(as_text=True)

    assert 'role="combobox"' in html
    assert 'role="listbox"' in html
    assert 'role="option"' in html
    # The static class is what makes the list readable in flow before JS runs;
    # combobox.js removes it once it owns the list.
    assert "combobox__list--static" in html
    # Each option carries a real form targeting its own document.
    assert f'action="/collections/{COLLECTION_ID}/documents/{DOC_ID}"' in html
    assert 'name="action" value="add"' in html
    assert 'name="csrf_token"' in html
    # And the text the filter matches on is in the markup, not derived in JS.
    assert 'data-label="Dune pdf"' in html


def test_the_add_picker_is_absent_when_there_is_nothing_to_add(monkeypatch):
    """An empty picker is a control that cannot do anything."""

    def fake_q1(sql, params=()):
        if "FROM document_collections WHERE id" in " ".join(sql.split()):
            return {
                "id": COLLECTION_ID,
                "name": "Everything",
                "kind": "manual",
                "description": None,
                "is_scope": False,
                "parent_lens_id": None,
                "rule": {},
            }
        return None

    monkeypatch.setattr(webapp, "q1", fake_q1)
    monkeypatch.setattr(webapp, "q", lambda sql, params=(): [])
    monkeypatch.setattr(webapp, "gql", lambda *a, **k: {"collections": []})
    webapp.app.config.update(TESTING=True)

    html = webapp.app.test_client().get(f"/collections/{COLLECTION_ID}").get_data(as_text=True)

    assert "data-combobox" not in html


def test_the_combobox_moves_a_highlight_without_moving_focus():
    """Focus has to stay in the input or the user cannot keep typing."""
    js = (_STATIC / "combobox.js").read_text(encoding="utf-8")

    assert "aria-activedescendant" in js
    assert "ArrowDown" in js and "ArrowUp" in js
    assert "Escape" in js and "Enter" in js


def test_the_row_filter_hides_rows_from_assistive_tech_too():
    """A filtered-out row is out of the list, not merely invisible."""
    js = (_STATIC / "row-filter.js").read_text(encoding="utf-8")

    assert "row.hidden = !match" in js


# --------------------------------------------------------------------------
# The meter
# --------------------------------------------------------------------------


def _render(source: str, **context) -> str:
    with webapp.app.app_context():
        return webapp.app.jinja_env.from_string(source).render(**context)


def test_meter_draws_geometry_as_svg_not_as_inline_style():
    html = _render('{% import "_components.html" as ui %}{{ ui.meter(3, 4, "Share") }}')

    assert 'width="75.0"' in html
    assert "style=" not in html


def test_meter_states_its_numbers_in_the_accessible_name():
    html = _render('{% import "_components.html" as ui %}{{ ui.meter(3, 4, "Connections") }}')

    assert 'aria-label="Connections: 3 of 4 (75.0%)"' in html


def test_meter_survives_a_zero_denominator():
    """An empty corpus must render an empty track, not a ZeroDivisionError."""
    html = _render('{% import "_components.html" as ui %}{{ ui.meter(0, 0, "Nothing") }}')

    assert 'width="0"' in html
    assert "(0%)" in html


def test_meter_can_omit_a_value_the_row_already_shows():
    html = _render(
        '{% import "_components.html" as ui %}{{ ui.meter(1, 2, "X", show_value=False) }}'
    )

    assert "meter__val" not in html


# --------------------------------------------------------------------------
# The inline-style budget and the policy that depends on it
# --------------------------------------------------------------------------


def test_no_template_carries_a_style_element():
    """Eleven per-template <style> blocks became rules in design-system.css. A
    new one would silently need the inline-style allowance back."""
    offenders = [
        p.name for p in _TEMPLATES.glob("*.html") if "<style>" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, offenders


def test_only_the_pdf_overlay_still_needs_an_inline_style_attribute():
    """One exception, and it is geometry: overlay_box_style computes left/top/
    width/height per word box from its OCR bbox, hundreds per page. Everything
    else is a class or an SVG attribute."""
    found = {}
    for path in _TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        # Ignore prose in comments that merely mentions the attribute.
        hits = re.findall(r"<[a-zA-Z][^>]*\sstyle=\"[^\"]*\"", text)
        if hits:
            found[path.name] = hits
    assert set(found) == {"document.html"}, found
    assert found["document.html"] == [
        '<a href="{{ b.href }}" class="overlay-box {{ b.class_name }}"\n       style="{{ b.style }}"'
    ]


def test_the_strict_policy_forbids_inline_style():
    assert "style-src 'self' https://fonts.googleapis.com" in CSP
    assert "'unsafe-inline'" not in CSP.split("style-src")[1]


def test_the_reader_is_the_only_route_granted_inline_style():
    assert csp_for_endpoint("documents") == CSP
    assert csp_for_endpoint("upload_form") == CSP
    assert csp_for_endpoint("collections") == CSP
    assert csp_for_endpoint(None) == CSP
    assert csp_for_endpoint("document") == CSP_INLINE_STYLE


def test_the_strict_policy_is_what_ordinary_pages_actually_receive(client):
    response = client.get("/upload")

    assert response.headers["Content-Security-Policy"] == CSP
    assert (
        "'unsafe-inline'" not in response.headers["Content-Security-Policy"].split("style-src")[1]
    )


def test_the_font_host_allowance_is_not_an_inline_allowance():
    """Google Fonts serves a stylesheet from another origin; that is a host
    allowance and has nothing to do with inline style."""
    assert "https://fonts.googleapis.com" in CSP
    assert "https://fonts.gstatic.com" in CSP  # the font files themselves
