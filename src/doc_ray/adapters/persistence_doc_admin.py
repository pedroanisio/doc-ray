"""Operator document-admin writes: metadata edits, tag merges, lifecycle.

Split out of :mod:`doc_ray.adapters.persistence` (D-015). Every change is
recorded in ``document_metadata_history`` (BL-058/BL-059); lifecycle states
are the reversible operator lifecycle, distinct from ingestion ``status``.
"""

from __future__ import annotations

from doc_ray.adapters.persistence_geometry import DictConnection


def _author_id(conn: DictConnection, name: str) -> int:
    """Upsert an author by name and return its id."""
    row = conn.execute(
        "INSERT INTO authors (name) VALUES (%s) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (name,),
    ).fetchone()
    assert row is not None  # RETURNING guarantees a row
    return int(row["id"])


def _tag_id(conn: DictConnection, tag: str) -> int:
    """Upsert a tag and return its id."""
    row = conn.execute(
        "INSERT INTO tags (tag) VALUES (%s) "
        "ON CONFLICT (tag) DO UPDATE SET tag = EXCLUDED.tag RETURNING id",
        (tag,),
    ).fetchone()
    assert row is not None  # RETURNING guarantees a row
    return int(row["id"])


def update_document_metadata(
    conn: DictConnection,
    document_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    actor: str | None = None,
) -> list[str]:
    """Edit a document's title/description/tags, recording one history row per change.

    Only fields actually passed (not ``None``) are considered; a field whose new
    value equals its current value is not treated as a change (no-op, no history
    row). ``tags``, when passed, fully replaces the document's tag set.

    Args:
        conn: Open database connection.
        document_id: The document to edit.
        title: New title, or ``None`` to leave unchanged.
        description: New description, or ``None`` to leave unchanged.
        tags: New complete tag set, or ``None`` to leave unchanged.
        actor: Caller-supplied label recorded on each history row.

    Returns:
        The list of field names actually changed (a subset of
        ``["title", "description", "tags"]``), in that order.

    Raises:
        ValueError: If *document_id* does not name an existing document.
    """
    from doc_ray.domain.ulid import new_ulid_str

    changed: list[str] = []
    with conn.transaction():
        current = conn.execute(
            "SELECT title, description FROM documents WHERE id = %s FOR UPDATE",
            (document_id,),
        ).fetchone()
        if current is None:
            raise ValueError(f"no such document: {document_id}")
        current_tags = None
        if tags is not None:
            current_tags = sorted(
                r["tag"]
                for r in conn.execute(
                    "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
                    "WHERE dt.document_id = %s",
                    (document_id,),
                ).fetchall()
            )

        def _record(field: str, old_value: str | None, new_value: str | None) -> None:
            conn.execute(
                "INSERT INTO document_metadata_history "
                "(id, document_id, field, old_value, new_value, actor) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (new_ulid_str(), document_id, field, old_value, new_value, actor),
            )
            changed.append(field)

        if title is not None and title != current["title"]:
            _record("title", current["title"], title)
            conn.execute("UPDATE documents SET title = %s WHERE id = %s", (title, document_id))

        if description is not None and description != current["description"]:
            _record("description", current["description"], description)
            conn.execute(
                "UPDATE documents SET description = %s WHERE id = %s", (description, document_id)
            )

        if tags is not None:
            new_tags = sorted({t for t in tags if t})
            if new_tags != current_tags:
                _record("tags", ", ".join(current_tags or []), ", ".join(new_tags))
                conn.execute("DELETE FROM document_tags WHERE document_id = %s", (document_id,))
                for tag in new_tags:
                    conn.execute(
                        "INSERT INTO document_tags (document_id, tag_id) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (document_id, _tag_id(conn, tag)),
                    )
    return changed


def add_document_tags(
    conn: DictConnection,
    document_id: str,
    tags: list[str],
    *,
    actor: str | None = None,
) -> list[str]:
    """Merge tags into a document's current tag set and record one tags change.

    Bulk document actions use this worker-time helper instead of computing a
    replacement tag list in the request. Empty/duplicate tag input is ignored.
    """
    new_tags = sorted({t.strip() for t in tags if t and t.strip()})
    if not new_tags:
        return []
    current_tags = sorted(
        r["tag"]
        for r in conn.execute(
            "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
            "WHERE dt.document_id = %s",
            (document_id,),
        ).fetchall()
    )
    return update_document_metadata(
        conn,
        document_id,
        tags=sorted(set(current_tags).union(new_tags)),
        actor=actor,
    )


def set_document_lifecycle(
    conn: DictConnection,
    document_id: str,
    lifecycle_state: str,
    *,
    actor: str | None = None,
) -> bool:
    """Set a document's lifecycle state and record the transition in history.

    ``documents.status`` remains the ingestion state; this function manages the
    reversible operator lifecycle added by BL-059. ``deleted`` is a soft delete:
    the document row and blob pointer remain available for restore.
    """
    from doc_ray.domain.ulid import new_ulid_str

    if lifecycle_state not in {"active", "archived", "deleted"}:
        raise ValueError(f"unknown document lifecycle state: {lifecycle_state}")

    with conn.transaction():
        current = conn.execute(
            "SELECT lifecycle_state FROM documents WHERE id = %s FOR UPDATE",
            (document_id,),
        ).fetchone()
        if current is None:
            raise ValueError(f"no such document: {document_id}")
        old_state = current["lifecycle_state"]
        if old_state == lifecycle_state:
            return False

        if lifecycle_state == "active":
            conn.execute(
                "UPDATE documents SET lifecycle_state = 'active', archived_at = NULL, "
                "deleted_at = NULL WHERE id = %s",
                (document_id,),
            )
        elif lifecycle_state == "archived":
            conn.execute(
                "UPDATE documents SET lifecycle_state = 'archived', archived_at = now(), "
                "deleted_at = NULL WHERE id = %s",
                (document_id,),
            )
        else:
            conn.execute(
                "UPDATE documents SET lifecycle_state = 'deleted', deleted_at = now() "
                "WHERE id = %s",
                (document_id,),
            )

        conn.execute(
            "INSERT INTO document_metadata_history "
            "(id, document_id, field, old_value, new_value, actor) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                new_ulid_str(),
                document_id,
                "lifecycle_state",
                old_state,
                lifecycle_state,
                actor,
            ),
        )
    return True
