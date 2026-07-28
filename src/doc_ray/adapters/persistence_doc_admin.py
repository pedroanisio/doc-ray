"""Operator document-admin writes: metadata edits, tag merges, lifecycle.

Split out of :mod:`doc_ray.adapters.persistence` (D-015). Every change is
recorded in ``document_metadata_history`` (BL-058/BL-059); lifecycle states
are the reversible operator lifecycle, distinct from ingestion ``status``.
"""

from __future__ import annotations

from doc_ray.adapters.persistence_geometry import DictConnection
from doc_ray.domain.indexed_text import bound_for_index

#: Every ``document_metadata_history.field`` value this module can write.
#:
#: This is the code side of a two-sided contract: the table carries a CHECK
#: constraint listing the same vocabulary, so a field recorded here that the
#: constraint does not admit aborts the whole metadata job. That is exactly how
#: ``authors`` broke in production — it was added to the writer while the CHECK
#: (migration 053) still listed four fields. Widening one side now requires
#: widening the other; ``test_metadata_history_contract.py`` fails the build if
#: they ever drift again. Keep in sync with the CHECK in migration 118.
HISTORY_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "tags",
    "authors",
    "lifecycle_state",
)


def _author_id(conn: DictConnection, name: str) -> int:
    """Upsert an author by name and return its id.

    The name is bounded to what ``authors_name_key`` can index. Document
    metadata is not length-controlled at the source — a PDF may put an entire
    author list in its ``/Author`` field — and an oversized value used to abort
    the whole ingest at the persist step, discarding all the extraction work
    before it. See :mod:`doc_ray.domain.indexed_text`.
    """
    row = conn.execute(
        "INSERT INTO authors (name) VALUES (%s) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        (bound_for_index(name),),
    ).fetchone()
    assert row is not None  # RETURNING guarantees a row
    return int(row["id"])


def _tag_id(conn: DictConnection, tag: str) -> int:
    """Upsert a tag and return its id.

    Bounded for the same reason as :func:`_author_id`: ``tags.tag`` is the same
    unbounded ``text`` under the same kind of UNIQUE btree.
    """
    row = conn.execute(
        "INSERT INTO tags (tag) VALUES (%s) "
        "ON CONFLICT (tag) DO UPDATE SET tag = EXCLUDED.tag RETURNING id",
        (bound_for_index(tag),),
    ).fetchone()
    assert row is not None  # RETURNING guarantees a row
    return int(row["id"])


def _dedup_ordered(names: list[str]) -> list[str]:
    """Strip, drop blanks, and dedupe *names* preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        name = raw.strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _replace_authors(conn: DictConnection, document_id: str, new_authors: list[str]) -> None:
    """Rewrite a document's ordered author set (positions 0..n)."""
    conn.execute("DELETE FROM document_authors WHERE document_id = %s", (document_id,))
    for position, name in enumerate(new_authors):
        conn.execute(
            "INSERT INTO document_authors (document_id, author_id, position) VALUES (%s, %s, %s)",
            (document_id, _author_id(conn, name), position),
        )


def update_document_metadata(
    conn: DictConnection,
    document_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    authors: list[str] | None = None,
    actor: str | None = None,
) -> list[str]:
    """Edit a document's title/description/tags/authors, one history row per change.

    Only fields actually passed (not ``None``) are considered; a field whose new
    value equals its current value is not treated as a change (no-op, no history
    row). ``tags`` (a set) and ``authors`` (an ordered list — author order is
    meaningful) each fully replace the document's current set when passed.

    Args:
        conn: Open database connection.
        document_id: The document to edit.
        title: New title, or ``None`` to leave unchanged.
        description: New description, or ``None`` to leave unchanged.
        tags: New complete tag set, or ``None`` to leave unchanged.
        authors: New complete ordered author list, or ``None`` to leave unchanged.
        actor: Caller-supplied label recorded on each history row.

    Returns:
        The list of field names actually changed (a subset of
        ``["title", "description", "tags", "authors"]``), in that order.

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
        current_authors = None
        if authors is not None:
            current_authors = [
                r["name"]
                for r in conn.execute(
                    "SELECT a.name FROM document_authors da JOIN authors a ON a.id = da.author_id "
                    "WHERE da.document_id = %s ORDER BY da.position",
                    (document_id,),
                ).fetchall()
            ]

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

        if authors is not None:
            new_authors = _dedup_ordered(authors)
            if new_authors != current_authors:
                _record("authors", ", ".join(current_authors or []), ", ".join(new_authors))
                _replace_authors(conn, document_id, new_authors)
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
