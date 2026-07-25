"""Audited lens-registry write routes for collections and documents.

Collection reads remain in ``collections_web`` and the reader's valid lens
options are direct, RLS-scoped SQL. Promotion, demotion, and default selection
always cross GraphQL because they are curation decisions with authorization and
audit requirements.
"""

from __future__ import annotations

from types import ModuleType

from flask import current_app, redirect, request, url_for

_core: ModuleType


def collection_lens_update(collection_id):
    """Promote one collection to a lens or demote an existing lens."""
    action = request.form.get("action", "")
    variables = {"collectionId": str(collection_id)}
    try:
        if action == "promote":
            variables["parentLensId"] = request.form.get("parent_lens_id") or None
            data = _core.gql(
                """mutation($collectionId: ID!, $parentLensId: ID) {
                  promoteCollectionToLens(collectionId: $collectionId,
                    parentLensId: $parentLensId) { id isScope parentLensId }
                }""",
                variables,
            )
            result = data["promoteCollectionToLens"]
            notice = f"collection {result['id']} promoted to a lens"
        elif action == "demote":
            data = _core.gql(
                """mutation($collectionId: ID!) {
                  demoteLens(collectionId: $collectionId) { id isScope }
                }""",
                variables,
            )
            notice = f"lens {data['demoteLens']['id']} demoted to a collection"
        else:
            raise ValueError("unknown lens action")
        return redirect(url_for("collection_detail", collection_id=collection_id, notice=notice))
    except Exception as exc:  # noqa: BLE001 - API guard failures belong on the collection page
        current_app.logger.warning("lens update failed: %s", exc)
        return redirect(url_for("collection_detail", collection_id=collection_id, error=str(exc)))


def document_default_lens(doc_id):
    """Set or clear a document's default lens through the guarded API mutation."""
    lens_id = request.form.get("lens_id") or None
    try:
        _core.gql(
            """mutation($documentId: ID!, $lensId: ID) {
              setDocumentDefaultLens(documentId: $documentId, lensId: $lensId) {
                id defaultLensId
              }
            }""",
            {"documentId": str(doc_id), "lensId": lens_id},
        )
        return redirect(url_for("document", doc_id=doc_id, lens_notice="default lens updated"))
    except Exception as exc:  # noqa: BLE001 - membership/scope denials render beside the picker
        current_app.logger.warning("default lens update failed: %s", exc)
        return redirect(url_for("document", doc_id=doc_id, lens_error=str(exc)))


def register(app, core: ModuleType) -> None:
    """Attach lens writes to *app* with late-bound helpers."""
    global _core
    _core = core
    app.add_url_rule(
        "/collections/<uuid:collection_id>/lens",
        "collection_lens_update",
        collection_lens_update,
        methods=["POST"],
    )
    app.add_url_rule(
        "/doc/<uuid:doc_id>/default-lens",
        "document_default_lens",
        document_default_lens,
        methods=["POST"],
    )
