"""Operator-gated pages (health, metadata correction) and backup routes.

Split out of ``webapp/app.py`` (D-015). Query/GraphQL helpers are resolved
through the ``webapp.app`` module object at call time so the test suite's
monkeypatching keeps reaching the routes that moved here.
"""

from __future__ import annotations

from types import ModuleType

from flask import (
    Response,
    abort,
    current_app,
    jsonify,
    redirect,
    request,
    stream_with_context,
    url_for,
)

try:
    from webapp.document_metadata import submit_metadata_update
    from webapp.graphql_client import API_URL, api_auth_headers
    from webapp.health_contract import operator_health_payload
    from webapp.operator_health import build_operator_health, require_operator_health
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from document_metadata import submit_metadata_update
    from graphql_client import API_URL, api_auth_headers
    from health_contract import operator_health_payload
    from operator_health import build_operator_health, require_operator_health

_core: ModuleType


def operator_health():
    """Render read-only corpus and database health snapshots for operators."""
    require_operator_health()
    return _core.render_template("operator_health.html", **build_operator_health(_core.q, _core.q1))


def operator_health_json():
    """Return the stable operator-health JSON contract for operators."""
    require_operator_health()
    return jsonify(operator_health_payload(build_operator_health(_core.q, _core.q1)))


def operator_document_metadata(doc_id):
    """Render an operator-gated document metadata correction form."""
    require_operator_health()
    doc = _core.q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if not doc:
        return ("Document not found", 404)
    tags = _core.q(
        "SELECT tag FROM document_tags WHERE document_id = %s ORDER BY tag", (str(doc_id),)
    )
    metadata_history = _core.q(
        "SELECT field, old_value, new_value, changed_at, actor "
        "FROM document_metadata_history WHERE document_id = %s ORDER BY id DESC",
        (str(doc_id),),
    )
    return _core.render_template(
        "operator_metadata.html",
        doc=doc,
        tags=[t["tag"] for t in tags],
        metadata_history=metadata_history,
        issue=request.args.get("issue"),
        metadata_error=request.args.get("metadata_error"),
        metadata_queued=request.args.get("metadata_queued"),
    )


def operator_document_metadata_update(doc_id):
    """Submit an operator-gated metadata correction through BL-058's mutation."""
    require_operator_health()
    error = submit_metadata_update(_core.gql, doc_id, request.form)
    if error is not None:
        return redirect(url_for("operator_document_metadata", doc_id=doc_id, metadata_error=error))
    return redirect(url_for("operator_document_metadata", doc_id=doc_id, metadata_queued=1))


def backups():
    """List backup archives with a create button and per-row restore/download."""
    rows = _core.q(
        "SELECT id, created_at, status, object_key, archive_bytes, table_rows, "
        "       blob_count, doc_count, note, error FROM backups ORDER BY created_at DESC"
    )
    return _core.render_template("backups.html", rows=rows, error=request.args.get("error"))


def backups_create():
    """Enqueue a full backup via the GraphQL API, then follow its job."""
    note = (request.form.get("note") or "").strip() or None
    try:
        data = _core.gql("mutation($n: String){ createBackup(note: $n){ id } }", {"n": note})
    except Exception as exc:
        current_app.logger.exception("backup creation failed")
        return redirect(url_for("backups", error=str(exc)))
    return redirect(url_for("job_status", job_id=data["createBackup"]["id"]))


def backups_restore(backup_id):
    """Enqueue a restore of the whole corpus from *backup_id* (destructive)."""
    try:
        data = _core.gql(
            "mutation($id: ID!){ restoreBackup(backupId: $id){ id } }", {"id": backup_id}
        )
    except Exception as exc:
        current_app.logger.exception("backup restore failed")
        return redirect(url_for("backups", error=str(exc)))
    return redirect(url_for("job_status", job_id=data["restoreBackup"]["id"]))


def backups_download(backup_id):
    """Stream a backup archive to the browser, proxied from the API.

    The API owns object-store access (it lives inside the store's network); the
    webapp never reaches S3 directly, so a host-vs-container ``S3_ENDPOINT``
    mismatch cannot break downloads. The archive is streamed chunk-by-chunk so a
    large corpus never has to be buffered whole in the webapp. The download is
    admin-only at the API (BL-094), so the same credential every other write
    forward uses (``api_auth_headers``) rides along here too; a 401/403/404
    from the API propagates as the same status rather than a proxy 500.
    """
    import httpx

    base = API_URL.rsplit("/graphql", 1)[0]
    url = f"{base}/backups/{backup_id}/download"
    client = httpx.Client(timeout=None)
    upstream = client.send(
        client.build_request("GET", url, headers=api_auth_headers()), stream=True
    )
    if upstream.status_code in (401, 403, 404):
        status = upstream.status_code
        upstream.close()
        client.close()
        abort(status)
    upstream.raise_for_status()

    def _pump():
        try:
            yield from upstream.iter_bytes(chunk_size=1 << 20)
        finally:
            upstream.close()
            client.close()

    return Response(
        stream_with_context(_pump()),
        mimetype="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{backup_id}.tar.gz"'},
    )


def register(app, core: ModuleType) -> None:
    """Attach the operator and backup routes to *app*, late-binding *core* (D-015 split)."""
    global _core
    _core = core
    app.add_url_rule("/operator/health", "operator_health", operator_health)
    app.add_url_rule("/operator/health.json", "operator_health_json", operator_health_json)
    app.add_url_rule(
        "/operator/doc/<uuid:doc_id>/metadata",
        "operator_document_metadata",
        operator_document_metadata,
    )
    app.add_url_rule(
        "/operator/doc/<uuid:doc_id>/metadata",
        "operator_document_metadata_update",
        operator_document_metadata_update,
        methods=["POST"],
    )
    app.add_url_rule("/backups", "backups", backups)
    app.add_url_rule("/backups", "backups_create", backups_create, methods=["POST"])
    app.add_url_rule(
        "/backups/<backup_id>/restore", "backups_restore", backups_restore, methods=["POST"]
    )
    app.add_url_rule("/backups/<backup_id>/download", "backups_download", backups_download)
