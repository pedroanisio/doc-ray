"""Owner-driven document sharing UI (Phase 3), a self-contained webapp module.

Kept out of ``document_web`` deliberately: that module is already at the D-015
size cap, and sharing is a distinct concern. The browser never touches
``document_acl`` directly — every read and write goes through the GraphQL API
under the signed-in principal's forwarded token, so the same RLS policies that
guard the API guard the UI. The page renders nothing and offers no controls to a
principal who may not share, because ``documentShares`` returns an empty list and
``shareDocument`` is refused server-side.
"""

from __future__ import annotations

from types import ModuleType

from flask import abort, current_app, redirect, request, url_for

_core: ModuleType

_LIST_SHARES = """
query DocumentShares($id: ID!) {
  documentShares(id: $id) { principalId email permission grantedAt }
}
"""

_SHARE = """
mutation ShareDocument($id: ID!, $email: String!, $permission: String!) {
  shareDocument(documentId: $id, email: $email, permission: $permission) { permission }
}
"""

_REVOKE = """
mutation RevokeDocumentShare($id: ID!, $email: String!) {
  revokeDocumentShare(documentId: $id, email: $email)
}
"""

_SET_VISIBILITY = """
mutation SetDocumentVisibility($id: ID!, $visibility: String!) {
  setDocumentVisibility(documentId: $id, visibility: $visibility)
}
"""


def document_sharing(doc_id):
    """Render the sharing panel: org visibility, current grants, and a grant form."""
    doc = _core.q1("SELECT id, title, visibility FROM documents WHERE id = %s", (str(doc_id),))
    if doc is None:
        abort(404, "Document not found.")
    shares: list[dict] = []
    error = request.args.get("error")
    try:
        data = _core.gql(_LIST_SHARES, {"id": str(doc_id)})
        shares = data.get("documentShares") or []
    except Exception as exc:  # API unreachable or the caller may not view grants
        current_app.logger.exception("listing document shares failed")
        error = error or str(exc)
    return _core.render_template(
        "sharing.html",
        doc=doc,
        shares=shares,
        error=error,
        shared=request.args.get("shared"),
        revoked=request.args.get("revoked"),
        visibility_set=request.args.get("visibility"),
    )


def document_set_visibility(doc_id):
    """Publish the document to the tenant (org) or make it private again."""
    visibility = (request.form.get("visibility") or "").strip().lower()
    if visibility not in ("private", "org"):
        return redirect(url_for("document_sharing", doc_id=doc_id, error="invalid visibility"))
    try:
        _core.gql(_SET_VISIBILITY, {"id": str(doc_id), "visibility": visibility})
    except Exception as exc:
        return redirect(url_for("document_sharing", doc_id=doc_id, error=str(exc)))
    return redirect(url_for("document_sharing", doc_id=doc_id, visibility=visibility))


def document_share_submit(doc_id):
    """Grant read/annotate to a colleague by email via the GraphQL mutation."""
    email = (request.form.get("email") or "").strip()
    permission = (request.form.get("permission") or "read").strip().lower()
    if not email:
        return redirect(url_for("document_sharing", doc_id=doc_id, error="enter an email"))
    try:
        _core.gql(_SHARE, {"id": str(doc_id), "email": email, "permission": permission})
    except Exception as exc:
        # The API's own message (validation / not-owner / unknown email) is safe.
        return redirect(url_for("document_sharing", doc_id=doc_id, error=str(exc)))
    return redirect(url_for("document_sharing", doc_id=doc_id, shared=email))


def document_share_revoke(doc_id):
    """Revoke every grant a colleague holds on the document."""
    email = (request.form.get("email") or "").strip()
    if not email:
        return redirect(url_for("document_sharing", doc_id=doc_id, error="enter an email"))
    try:
        _core.gql(_REVOKE, {"id": str(doc_id), "email": email})
    except Exception as exc:
        return redirect(url_for("document_sharing", doc_id=doc_id, error=str(exc)))
    return redirect(url_for("document_sharing", doc_id=doc_id, revoked=email))


def register(app, core: ModuleType) -> None:
    """Attach the sharing routes to *app*, late-binding *core* (D-015 split)."""
    global _core
    _core = core
    app.add_url_rule("/doc/<uuid:doc_id>/sharing", "document_sharing", document_sharing)
    app.add_url_rule(
        "/doc/<uuid:doc_id>/sharing/visibility",
        "document_set_visibility",
        document_set_visibility,
        methods=["POST"],
    )
    app.add_url_rule(
        "/doc/<uuid:doc_id>/sharing/grant",
        "document_share_submit",
        document_share_submit,
        methods=["POST"],
    )
    app.add_url_rule(
        "/doc/<uuid:doc_id>/sharing/revoke",
        "document_share_revoke",
        document_share_revoke,
        methods=["POST"],
    )
