"""Drift guard: the metadata-history CHECK must admit every field the code writes.

The defect this prevents (found in production, 2026-07-24): editing a document's
``authors`` raised

    CheckViolation: new row for relation "document_metadata_history" violates
    check constraint "document_metadata_history_field_check"

because the authors-edit feature added ``_record("authors", ...)`` to
:mod:`doc_ray.adapters.persistence_doc_admin` while the constraint (last widened
by migration 053) still listed only title/description/tags/lifecycle_state. Every
metadata update that changed an author aborted the whole job.

Two independent things had to agree and nothing checked that they did. This test
is that check: it reads the live constraint out of the catalog and compares it to
:data:`~doc_ray.adapters.persistence_doc_admin.HISTORY_FIELDS`, the code's single
source of truth. Adding a new editable field without the matching migration now
fails here instead of in production.
"""

from __future__ import annotations

import re

import pytest

_itdb = pytest.importorskip("_itdb")

from doc_ray.adapters.persistence_doc_admin import HISTORY_FIELDS  # noqa: E402


@pytest.fixture
def conn():
    c = _itdb.connect()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _constraint_fields(conn) -> set[str]:
    """Return the field vocabulary the live CHECK constraint admits."""
    row = conn.execute(
        "SELECT pg_get_constraintdef(oid) AS d FROM pg_constraint "
        "WHERE conname = 'document_metadata_history_field_check'"
    ).fetchone()
    assert row is not None, "document_metadata_history_field_check is missing entirely"
    return set(re.findall(r"'([a-z_]+)'::text", row["d"]))


def test_check_constraint_admits_every_field_the_code_records(conn):
    """The constraint must be a superset of the code's editable-field vocabulary."""
    allowed = _constraint_fields(conn)
    missing = set(HISTORY_FIELDS) - allowed
    assert not missing, (
        f"document_metadata_history_field_check rejects {sorted(missing)}, which "
        f"persistence_doc_admin writes. Add a migration widening the CHECK — a "
        f"metadata edit touching those fields fails in production otherwise."
    )


def test_every_field_the_constraint_allows_is_declared_in_code(conn):
    """The reverse direction: no orphan value lingering in the constraint."""
    allowed = _constraint_fields(conn)
    orphans = allowed - set(HISTORY_FIELDS)
    assert not orphans, (
        f"the CHECK admits {sorted(orphans)} but HISTORY_FIELDS does not declare them; "
        f"the constraint and the code have drifted apart."
    )


@pytest.mark.parametrize("field", sorted(HISTORY_FIELDS))
def test_each_declared_field_is_actually_insertable(conn, field):
    """Insert one history row per declared field — the constraint's real behavior."""
    doc = _itdb.make_doc(conn, "Constraint contract fixture.")
    from doc_ray.domain.ulid import new_ulid_str

    conn.execute(
        "INSERT INTO document_metadata_history "
        "(id, document_id, field, old_value, new_value, actor) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (new_ulid_str(), doc, field, "old", "new", None),
    )
    got = conn.execute(
        "SELECT field FROM document_metadata_history WHERE document_id = %s", (doc,)
    ).fetchone()
    assert got["field"] == field
