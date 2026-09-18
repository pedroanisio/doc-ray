"""Async read adapter for document collections (BL-057).

Split out of :mod:`doc_ray.adapters.persistence_read` (D-015), which
re-exports everything here so the read surface stays one import. Same
contract: the caller owns the ``dict_row`` async connection; nothing here
opens, closes, or commits.
"""

from __future__ import annotations

import json
from typing import Any, cast

import psycopg

from doc_ray.adapters.read_cursors import decode_cursor, encode_cursor

Row = dict[str, Any]
_AConn = psycopg.AsyncConnection[Row]


async def _rows(conn: _AConn, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[Row]:
    """Execute *sql* and return all rows as dicts."""
    cur = await conn.execute(sql, params)
    return await cur.fetchall()


async def _row(conn: _AConn, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Row | None:
    """Execute *sql* and return the first row as a dict, or ``None``."""
    cur = await conn.execute(sql, params)
    return await cur.fetchone()


CollectionRuleInput = dict[str, object] | str | None


def _collection_rule(rule: CollectionRuleInput) -> dict[str, object]:
    """Return a collection rule as a dict regardless of DB driver representation."""
    if isinstance(rule, dict):
        return rule
    if isinstance(rule, str) and rule.strip():
        data = json.loads(rule)
        return cast(dict[str, object], data) if isinstance(data, dict) else {}
    return {}


def collection_rule_summary(rule: CollectionRuleInput) -> str:
    """Return a concise human-readable summary for a smart collection rule."""
    normalized = _collection_rule(rule)
    parts: list[str] = []
    if normalized.get("doc_type"):
        parts.append(f"type: {normalized['doc_type']}")
    if normalized.get("status"):
        parts.append(f"status: {normalized['status']}")
    tags = normalized.get("tags")
    if isinstance(tags, (list, tuple)) and tags:
        parts.append("tags: " + ", ".join(str(tag) for tag in tags))
    return "; ".join(parts) if parts else "all documents"


async def _manual_collection_count(conn: _AConn, collection_id: str) -> int:
    row = await _row(
        conn,
        """
        SELECT count(*) AS n
        FROM document_collection_members m
        JOIN documents d ON d.id = m.document_id
        WHERE m.collection_id = %s
        """,
        (collection_id,),
    )
    return int(row["n"]) if row else 0


async def _smart_collection_count(conn: _AConn, rule: CollectionRuleInput) -> int:
    normalized = _collection_rule(rule)
    row = await _row(
        conn,
        """
        SELECT count(*) AS n
        FROM documents d
        WHERE (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
          AND (%(status)s::text IS NULL OR d.status = %(status)s)
          AND (%(tags)s::text[] IS NULL OR EXISTS (
                SELECT 1 FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                WHERE dt.document_id = d.id AND t.tag = ANY(%(tags)s::text[])
              ))
        """,
        {
            "doc_type": normalized.get("doc_type"),
            "status": normalized.get("status"),
            "tags": normalized.get("tags"),
        },
    )
    return int(row["n"]) if row else 0


async def _attach_collection_projection(conn: _AConn, row: Row) -> Row:
    row["rule"] = _collection_rule(row.get("rule"))
    row["rule_summary"] = collection_rule_summary(row["rule"])
    if row.get("kind") == "manual":
        row["document_count"] = await _manual_collection_count(conn, str(row["id"]))
    else:
        row["document_count"] = await _smart_collection_count(conn, row["rule"])
    row["cursor"] = encode_cursor("collections", name=row["name"], id=row["id"])
    return row


async def list_collections(
    conn: _AConn,
    *,
    limit: int = 50,
    after: str | None = None,
    kind: str | None = None,
) -> list[Row]:
    """Return document collections, ordered by name, with visible document counts."""
    cursor = decode_cursor(after, "collections")
    rows = await _rows(
        conn,
        """
        SELECT id, name, description, kind, rule, is_scope, projects, parent_lens_id,
               created_at, updated_at
        FROM document_collections
        WHERE (%(kind)s::text IS NULL OR kind = %(kind)s)
          AND (%(after_name)s::text IS NULL OR name > %(after_name)s
               OR (name = %(after_name)s AND id > %(after_id)s::uuid))
        ORDER BY name, id
        LIMIT %(limit)s
        """,
        {
            "limit": limit,
            "kind": kind,
            "after_name": cursor["name"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    return [await _attach_collection_projection(conn, row) for row in rows]


async def list_lenses(
    conn: _AConn,
    *,
    limit: int = 50,
    after: str | None = None,
) -> list[Row]:
    """Return the scope collections (lenses), name-ordered and keyset-paginated.

    A *lens* is a ``document_collections`` row flagged ``is_scope``; this
    is ``list_collections`` narrowed to those rows. The partial index
    ``idx_document_collections_scope (name, id) WHERE is_scope`` (migration 121)
    serves both the predicate and the ORDER BY, so the scan stays O(page) at the
    10k-document target. ``document_count`` on each row is RLS-scoped to the
    caller's visible members; the lens rows themselves are shared corpus metadata.
    """
    cursor = decode_cursor(after, "collections")
    rows = await _rows(
        conn,
        """
        SELECT id, name, description, kind, rule, is_scope, projects, parent_lens_id,
               created_at, updated_at
        FROM document_collections
        WHERE is_scope
          AND (%(after_name)s::text IS NULL OR name > %(after_name)s
               OR (name = %(after_name)s AND id > %(after_id)s::uuid))
        ORDER BY name, id
        LIMIT %(limit)s
        """,
        {
            "limit": limit,
            "after_name": cursor["name"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    return [await _attach_collection_projection(conn, row) for row in rows]


async def get_collection(conn: _AConn, collection_id: str) -> Row | None:
    """Return one document collection with its visible document count."""
    row = await _row(
        conn,
        """
        SELECT id, name, description, kind, rule, is_scope, projects, parent_lens_id,
               created_at, updated_at
        FROM document_collections
        WHERE id = %s
        """,
        (collection_id,),
    )
    return await _attach_collection_projection(conn, row) if row else None


async def collections_for_document(conn: _AConn, document_id: str) -> list[Row]:
    """Return collections that include a visible document explicitly or by smart rule."""
    rows = await _rows(
        conn,
        """
        SELECT c.id, c.name, c.description, c.kind, c.rule, c.is_scope, c.projects,
               c.parent_lens_id, c.created_at, c.updated_at
        FROM document_collections c
        JOIN documents d ON d.id = %(doc)s::uuid
        WHERE (
            c.kind = 'manual'
            AND EXISTS (
                SELECT 1 FROM document_collection_members m
                WHERE m.collection_id = c.id AND m.document_id = d.id
            )
        ) OR (
            c.kind = 'smart'
            AND (NULLIF(c.rule->>'doc_type', '') IS NULL OR d.doc_type = c.rule->>'doc_type')
            AND (NULLIF(c.rule->>'status', '') IS NULL OR d.status = c.rule->>'status')
            AND (
                NOT (c.rule ? 'tags')
                OR EXISTS (
                    SELECT 1
                    FROM document_tags dt
                    JOIN tags t ON t.id = dt.tag_id
                    WHERE dt.document_id = d.id
                      AND t.tag = ANY(SELECT jsonb_array_elements_text(c.rule->'tags'))
                )
            )
        )
        ORDER BY c.name, c.id
        """,
        {"doc": document_id},
    )
    return [await _attach_collection_projection(conn, row) for row in rows]


async def list_collection_documents(
    conn: _AConn,
    collection: Row,
    *,
    limit: int = 50,
    after: str | None = None,
) -> list[Row]:
    """Return visible documents belonging to a manual or smart collection."""
    cursor = decode_cursor(after, "collection_documents")
    if collection["kind"] == "manual":
        rows = await _rows(
            conn,
            """
            SELECT d.id, d.title, d.original_filename AS filename, d.doc_type, d.language,
                   d.page_count, d.char_count, d.status, d.lifecycle_state,
                   d.archived_at, d.deleted_at, d.created_at,
                   (SELECT n_sentences FROM document_stats ds WHERE ds.document_id = d.id)
                       AS n_sentences,
                   (SELECT count(*) FROM document_provenance p WHERE p.document_id = d.id)
                       AS n_provenance,
                   m.added_at
            FROM document_collection_members m
            JOIN documents d ON d.id = m.document_id
            WHERE m.collection_id = %(collection)s::uuid
              AND (%(after_added)s::timestamptz IS NULL
                   OR m.added_at < %(after_added)s::timestamptz
                   OR (m.added_at = %(after_added)s::timestamptz
                       AND d.id > %(after_id)s::uuid))
            ORDER BY m.added_at DESC, d.id
            LIMIT %(limit)s
            """,
            {
                "collection": str(collection["id"]),
                "limit": limit,
                "after_added": cursor["added_at"] if cursor else None,
                "after_id": cursor["id"] if cursor else None,
            },
        )
        for row in rows:
            row["cursor"] = encode_cursor(
                "collection_documents", added_at=row["added_at"], id=row["id"]
            )
        return rows

    rule = _collection_rule(collection.get("rule"))
    rows = await _rows(
        conn,
        """
        SELECT d.id, d.title, d.original_filename AS filename, d.doc_type, d.language,
               d.page_count, d.char_count, d.status, d.lifecycle_state,
               d.archived_at, d.deleted_at, d.created_at,
               (SELECT n_sentences FROM document_stats ds WHERE ds.document_id = d.id)
                   AS n_sentences,
               (SELECT count(*) FROM document_provenance p WHERE p.document_id = d.id)
                   AS n_provenance
        FROM documents d
        WHERE (%(doc_type)s::text IS NULL OR d.doc_type = %(doc_type)s)
          AND (%(status)s::text IS NULL OR d.status = %(status)s)
          AND (%(tags)s::text[] IS NULL OR EXISTS (
                SELECT 1 FROM document_tags dt JOIN tags t ON t.id = dt.tag_id
                WHERE dt.document_id = d.id AND t.tag = ANY(%(tags)s::text[])
              ))
          AND (%(after_created)s::timestamptz IS NULL
               OR d.created_at < %(after_created)s::timestamptz
               OR (d.created_at = %(after_created)s::timestamptz
                   AND d.id > %(after_id)s::uuid))
        ORDER BY d.created_at DESC, d.id
        LIMIT %(limit)s
        """,
        {
            "limit": limit,
            "doc_type": rule.get("doc_type"),
            "status": rule.get("status"),
            "tags": rule.get("tags"),
            "after_created": cursor["created_at"] if cursor else None,
            "after_id": cursor["id"] if cursor else None,
        },
    )
    for row in rows:
        row["cursor"] = encode_cursor(
            "collection_documents", created_at=row["created_at"], id=row["id"]
        )
    return rows


# --------------------------------------------------------------------------
# Batched by-document readers (feed the API DataLoaders; avoid N+1)
