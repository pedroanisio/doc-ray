"""Document-collection helpers and routes (BL-057).

Split out of ``webapp/app.py`` (D-015). Query helpers (``q``/``q1``/
``exec_sql``) are resolved through the ``webapp.app`` module object at call
time so the test suite's monkeypatching keeps reaching the routes that moved
here. Direct SQL writes here are the webapp's one sanctioned exception to the
GraphQL write path (see BL-131's autocommit note on ``get_conn``).
"""

from __future__ import annotations

import json
from types import ModuleType

from flask import abort, current_app, redirect, request, url_for

try:
    from webapp.previews import document_preview
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from previews import document_preview

_core: ModuleType

_LENSES_QUERY = """
query WebappLenses {
  lenses(limit: 100) {
    id name description isScope parentLensId documentCount cursor
  }
}
"""


def _lenses() -> tuple[list[dict], str | None]:
    """Bind the public lens query and return a clean page-level error on failure."""
    try:
        return list(_core.gql(_LENSES_QUERY).get("lenses") or []), None
    except Exception as exc:  # noqa: BLE001 - registry failure must not blank collections
        current_app.logger.warning("lens registry unavailable: %s", exc)
        return [], str(exc)


def collection_rule_from_form(form) -> dict[str, object]:
    """Normalize smart-collection form fields into a compact JSON rule."""
    rule: dict[str, object] = {}
    doc_type = (form.get("doc_type") or "").strip().lower()
    status = (form.get("status") or "").strip().lower()
    tags = [tag.strip() for tag in (form.get("tags") or "").split(",") if tag.strip()]
    if doc_type:
        rule["doc_type"] = doc_type
    if status:
        rule["status"] = status
    if tags:
        rule["tags"] = tags
    return rule


def collection_rule(rule) -> dict[str, object]:
    """Return a collection rule as a dict regardless of DB driver representation."""
    if isinstance(rule, dict):
        return rule
    if isinstance(rule, str) and rule.strip():
        return json.loads(rule)
    return {}


def collection_rule_summary(rule) -> str:
    """Return a concise human label for a smart collection rule."""
    normalized = collection_rule(rule)
    parts = []
    if normalized.get("doc_type"):
        parts.append(f"type={normalized['doc_type']}")
    if normalized.get("status"):
        parts.append(f"status={normalized['status']}")
    if normalized.get("tags"):
        parts.append("tags=" + ", ".join(normalized["tags"]))
    return "; ".join(parts) if parts else "all documents"


def collection_document_rows(collection: dict) -> list[dict]:
    """Return the documents that belong to a manual or smart collection."""
    if collection["kind"] == "manual":
        rows = _core.q(
            """
            SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count,
                   d.status,
                   (SELECT n_sentences FROM document_stats ds WHERE ds.document_id = d.id)
                       AS n_sentences,
                   (SELECT string_agg(DISTINCT a.tool, ', ')
                      FROM annotations a WHERE a.document_id = d.id) AS tools
            FROM document_collection_members m
            JOIN documents d ON d.id = m.document_id
            WHERE m.collection_id = %s
            ORDER BY m.added_at DESC, d.title
            """,
            (str(collection["id"]),),
        )
    else:
        rule = collection_rule(collection.get("rule"))
        rows = _core.q(
            """
            SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count,
                   d.status,
                   (SELECT n_sentences FROM document_stats ds WHERE ds.document_id = d.id)
                       AS n_sentences,
                   (SELECT string_agg(DISTINCT a.tool, ', ')
                      FROM annotations a WHERE a.document_id = d.id) AS tools
            FROM documents d
            WHERE d.lifecycle_state <> 'deleted'
              AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
              AND (%(status)s::text IS NULL OR d.status = %(status)s)
              AND (%(tags)s::text[] IS NULL OR EXISTS (
                    SELECT 1 FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                    WHERE dt.document_id = d.id AND t.tag = ANY(%(tags)s::text[])
                  ))
            ORDER BY d.char_count DESC NULLS LAST, d.title
            LIMIT 200
            """,
            {
                "doc_type": rule.get("doc_type"),
                "status": rule.get("status"),
                "tags": rule.get("tags"),
            },
        )
    return [dict(row, preview=document_preview(row)) for row in rows]


def available_manual_collections(doc_id) -> list[dict]:
    """Return manual collections that do not already contain *doc_id*."""
    return _core.q(
        """
        SELECT c.id, c.name
        FROM document_collections c
        WHERE c.kind = 'manual'
          AND NOT EXISTS (
                SELECT 1 FROM document_collection_members m
                WHERE m.collection_id = c.id AND m.document_id = %s
              )
        ORDER BY c.name
        """,
        (str(doc_id),),
    )


def collections():
    """Render document collections and the create-collection form."""
    rows = _core.q(
        """
        SELECT c.id, c.name, c.description, c.kind, c.rule, c.is_scope,
               c.parent_lens_id, parent.name AS parent_lens_name,
               CASE
                 WHEN c.kind = 'manual' THEN (
                   SELECT count(*) FROM document_collection_members m
                   WHERE m.collection_id = c.id
                 )
                 ELSE NULL
               END AS document_count
        FROM document_collections c
        LEFT JOIN document_collections parent ON parent.id = c.parent_lens_id
        ORDER BY c.name
        """
    )
    for row in rows:
        if row.get("document_count") is None:
            row["document_count"] = _smart_collection_count(row)
        row["rule_summary"] = collection_rule_summary(row.get("rule"))
    lenses, lens_error = _lenses()
    return _core.render_template(
        "collections.html",
        rows=rows,
        lenses=lenses,
        error=request.args.get("error") or lens_error,
        notice=request.args.get("notice"),
    )


def _smart_collection_count(collection: dict) -> int:
    """Count a smart collection's documents without hydrating them.

    The landing page only needs the number; the old code ``len()``-ed a fully
    materialised 200-row page (previews + per-row subqueries) per collection.
    This counts over the small ``documents`` table with the same rule predicate.
    """
    rule = collection_rule(collection.get("rule"))
    row = _core.q1(
        """
        SELECT count(*) AS n
        FROM documents d
        WHERE d.lifecycle_state <> 'deleted'
          AND (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
          AND (%(status)s::text IS NULL OR d.status = %(status)s)
          AND (%(tags)s::text[] IS NULL OR EXISTS (
                SELECT 1 FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                WHERE dt.document_id = d.id AND t.tag = ANY(%(tags)s::text[])
              ))
        """,
        {
            "doc_type": rule.get("doc_type"),
            "status": rule.get("status"),
            "tags": rule.get("tags") or None,
        },
    )
    return int((row or {}).get("n") or 0)


def collections_create():
    """Create a manual collection or a smart collection rule."""
    kind = request.form.get("kind", "manual")
    if kind not in {"manual", "smart"}:
        return redirect(url_for("collections", error="unknown collection type"))
    rule = collection_rule_from_form(request.form) if kind == "smart" else {}
    row = _core.q1(
        """
        INSERT INTO document_collections (name, description, kind, rule)
        VALUES (%(name)s, %(description)s, %(kind)s, %(rule)s::jsonb)
        RETURNING id
        """,
        {
            "name": (request.form.get("name") or "").strip(),
            "description": (request.form.get("description") or "").strip(),
            "kind": kind,
            "rule": json.dumps(rule, sort_keys=True),
        },
    )
    return redirect(url_for("collection_detail", collection_id=row["id"]))


def collection_detail(collection_id):
    """Render one collection and its current documents."""
    collection = _core.q1("SELECT * FROM document_collections WHERE id = %s", (str(collection_id),))
    if not collection:
        abort(404, "Collection not found.")
    collection["rule_summary"] = collection_rule_summary(collection.get("rule"))
    documents_in_collection = collection_document_rows(collection)
    available = []
    if collection["kind"] == "manual":
        available = _core.q(
            """
            SELECT d.id, d.title, d.doc_type
            FROM documents d
            WHERE d.lifecycle_state <> 'deleted'
              AND NOT EXISTS (
                  SELECT 1 FROM document_collection_members m
                  WHERE m.collection_id = %s AND m.document_id = d.id
            )
            ORDER BY d.title
            LIMIT 100
            """,
            (str(collection_id),),
        )
    lenses, lens_error = _lenses()
    return _core.render_template(
        "collection.html",
        collection=collection,
        rows=documents_in_collection,
        available=available,
        lenses=[lens for lens in lenses if str(lens["id"]) != str(collection_id)],
        error=request.args.get("error") or lens_error,
        notice=request.args.get("notice"),
    )


def collection_document_update(collection_id, doc_id):
    """Add or remove one document from a manual collection."""
    action = request.form.get("action")
    if action == "add":
        _core.exec_sql(
            """
            INSERT INTO document_collection_members (collection_id, document_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (str(collection_id), str(doc_id)),
        )
    elif action == "remove":
        _core.exec_sql(
            "DELETE FROM document_collection_members WHERE collection_id = %s AND document_id = %s",
            (str(collection_id), str(doc_id)),
        )
    else:
        abort(400, "Unknown action.")
    return redirect(request.referrer or url_for("collection_detail", collection_id=collection_id))


def register(app, core: ModuleType) -> None:
    """Attach the collection routes to *app*, late-binding *core* (D-015 split)."""
    global _core
    _core = core
    app.add_url_rule("/collections", "collections", collections)
    app.add_url_rule("/collections", "collections_create", collections_create, methods=["POST"])
    app.add_url_rule("/collections/<uuid:collection_id>", "collection_detail", collection_detail)
    app.add_url_rule(
        "/collections/<uuid:collection_id>/documents/<uuid:doc_id>",
        "collection_document_update",
        collection_document_update,
        methods=["POST"],
    )
