"""Document intake: the upload form and its two submit paths.

Split out of ``webapp/corpus_web.py`` (D-015) when the asynchronous single-file
route landed — corpus browse was already at the net-LOC cap, and intake is a
distinct concern from browsing anyway.

There are two submit paths on purpose, and they are the same mutation:

``upload_submit`` (POST ``/upload``)
    The form post. One file *or* one reference, full page reload, redirect to
    the job page. It needs no JavaScript and is what the ``<form>`` in the
    template does on its own.

``upload_file`` (POST ``/upload/file``)
    One file, JSON reply. This exists so the browser can post several files and
    report progress per file, which a form post cannot do: a multipart form
    submission is one navigation with no progress events and no way to report
    that file three of five failed while the rest succeeded.

Both funnel into :func:`_ingest`, so the API contract, the error handling, and
the "the browser never touches the object store or the queue" rule are stated
once. Query/GraphQL helpers resolve through the ``webapp.app`` module object at
call time so the suite's monkeypatching keeps reaching these routes.
"""

from __future__ import annotations

import json
from types import ModuleType

from flask import current_app, jsonify, redirect, request, url_for

from doc_ray.domain.document_formats import ACCEPT_ATTRIBUTE, format_label

try:
    from webapp.graphql_client import API_URL, api_auth_headers
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from graphql_client import API_URL, api_auth_headers

_core: ModuleType

_FILE_MUTATION = (
    "mutation($file: Upload, $title: String, $pipeline: Boolean!,"
    " $extractor: String, $jurisdiction: String) {"
    " ingestDocument(file: $file, title: $title, pipeline: $pipeline,"
    " extractor: $extractor, jurisdiction: $jurisdiction) { id } }"
)
_REF_MUTATION = (
    "mutation($ref: String, $title: String, $pipeline: Boolean!) {"
    " ingestDocument(ref: $ref, title: $title, pipeline: $pipeline) { id } }"
)


def upload_form():
    """Render the document upload form.

    The accepted-format list is read from ``domain.document_formats`` rather
    than typed into the template: a hand-copied list drifts behind the
    extractor registry, and a narrower ``accept`` attribute hides working
    formats (HTML, SVG, PPTX) from the operator.
    """
    from doc_ray.adapters.patent_text_source_factory import patent_extraction_enabled

    return _core.render_template(
        "upload.html",
        error=request.args.get("error"),
        accept_attribute=ACCEPT_ATTRIBUTE,
        format_label=format_label(conjunction="or"),
        # Offered only where the deployment can honour it: a checkbox whose
        # submission the API rejects is worse than no checkbox.
        patent_extraction_enabled=patent_extraction_enabled(),
    )


def _ingest_options(form) -> dict:
    """Read the pipeline/extractor options shared by both submit paths."""
    # The column-aware reader is only meaningful for an uploaded PDF; a remote
    # reference resolves to whatever the source serves, so the checkbox does not
    # ride along that route.
    extractor = "patent" if form.get("patent") == "on" else None
    # Only meaningful alongside the patent reader; sent as None otherwise so the
    # API never records an office code against a document it did not steer.
    jurisdiction = (form.get("jurisdiction") or "").strip().upper() or None
    return {
        "title": form.get("title") or None,
        "pipeline": form.get("pipeline") == "on",
        "extractor": extractor,
        "jurisdiction": jurisdiction if extractor else None,
    }


def _post_kwargs(upload, ref: str, options: dict) -> dict:
    """Build the httpx kwargs for a file upload or a reference fetch."""
    if ref:
        return {
            "json": {
                "query": _REF_MUTATION,
                "variables": {
                    "ref": ref,
                    "title": options["title"],
                    "pipeline": options["pipeline"],
                },
            }
        }
    operations = {
        "query": _FILE_MUTATION,
        "variables": {"file": None, **options},
    }
    return {
        "data": {
            "operations": json.dumps(operations),
            "map": json.dumps({"0": ["variables.file"]}),
        },
        "files": {"0": (upload.filename, upload.stream, upload.mimetype)},
    }


def _ingest(upload, ref: str, options: dict) -> str:
    """Forward one intake to the ingest mutation and return its job id.

    Raises:
        RuntimeError: The API rejected the ingest, or returned no job. The
            message is our own API's validation text, which is safe to show.
        httpx.HTTPError: The API was unreachable. Transport detail (URLs, stack
            text) belongs in the log, not in the response.
    """
    import httpx

    response = httpx.post(
        API_URL,
        headers=api_auth_headers(),
        timeout=120.0,
        **_post_kwargs(upload, ref, options),
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
    job_id = ((body.get("data") or {}).get("ingestDocument") or {}).get("id")
    if not job_id:
        raise RuntimeError("the API returned no job for this ingest")
    return job_id


def upload_submit():
    """Send an uploaded file *or* a remote reference to the ingest mutation.

    Two intake routes, one mutation. A file goes as a GraphQL multipart upload;
    a reference goes as a plain JSON mutation, because there is no file part to
    map and the *worker* performs the fetch — so the browser is never held open
    on a remote download.

    The full pipeline (ingest -> annotate -> canonicalize) is enqueued by
    default; unticking the checkbox ingests only. The browser never touches the
    object store or the queue — the API is the one write path.
    """
    import httpx

    upload = request.files.get("file")
    has_file = upload is not None and bool(upload.filename)
    ref = (request.form.get("ref") or "").strip()
    if has_file and ref:
        return redirect(url_for("upload_form", error="give a file or a reference, not both"))
    if not has_file and not ref:
        return redirect(url_for("upload_form", error="choose a file or paste a reference first"))
    try:
        job_id = _ingest(upload, ref, _ingest_options(request.form))
    except httpx.HTTPError:
        current_app.logger.exception("ingest forwarding failed")
        return redirect(url_for("upload_form", error="ingest API unreachable — is it running?"))
    except Exception as exc:
        current_app.logger.exception("ingest rejected")
        return redirect(url_for("upload_form", error=str(exc)))
    return redirect(url_for("job_status", job_id=job_id))


def upload_file():
    """Ingest exactly one file and answer with JSON.

    The asynchronous sibling of :func:`upload_submit`, and the reason the
    multi-file queue can exist: it reports the outcome of *this* file, so the
    browser can show five files each with their own progress bar and their own
    verdict instead of one navigation that either works or does not.

    It is a strict subset — one file, no reference — because a reference fetch
    has no upload progress to report and is already instant from the browser's
    side. CSRF is the same session token as the form path, sent in the FormData.

    Returns:
        200 ``{"jobId", "job", "name"}`` on success; 400 ``{"error", "name"}``
        if no file was sent or the API rejected it; 502 ``{"error", "name"}`` if
        the API was unreachable.
    """
    import httpx

    upload = request.files.get("file")
    name = (upload.filename if upload else "") or ""
    if upload is None or not upload.filename:
        return jsonify({"error": "no file in this request", "name": name}), 400
    try:
        job_id = _ingest(upload, "", _ingest_options(request.form))
    except httpx.HTTPError:
        current_app.logger.exception("ingest forwarding failed")
        return jsonify({"error": "ingest API unreachable", "name": name}), 502
    except Exception as exc:
        current_app.logger.exception("ingest rejected")
        return jsonify({"error": str(exc), "name": name}), 400
    return jsonify({"jobId": job_id, "job": url_for("job_status", job_id=job_id), "name": name})


def register(app, core: ModuleType) -> None:
    """Attach the intake routes to *app*, late-binding *core* (D-015 split)."""
    global _core
    _core = core
    app.add_url_rule("/upload", "upload_form", upload_form)
    app.add_url_rule("/upload", "upload_submit", upload_submit, methods=["POST"])
    app.add_url_rule("/upload/file", "upload_file", upload_file, methods=["POST"])
