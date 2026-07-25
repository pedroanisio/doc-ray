"""Close the docray_client SELECT-grant gap class, and its resolution_review instance (#15).

Migration 117 fixed ``form_index`` for the structure read path (``entity_profiles``).
The "broader sweep" the issue asks for surfaces a second instance:
``resolution_review`` — read by the ``resolutionReview`` GraphQL query via
:func:`canonical_read.list_resolution_review` — was created (migration 023) after
*both* the 018 grant loop and the RLS rollout, so it had **neither** a
``docray_client`` grant **nor** RLS. Every real caller got
``permission denied for table resolution_review``.

The first test is the class-closing check: it reads the tables the client-facing
read adapters actually touch and asserts ``docray_client`` holds SELECT on each,
so the next table added after a grant loop cannot silently repeat this. The rest
exercise ``resolution_review`` under the client role and prove the fix is *scoped*
(a bare grant would leak every tenant's review queue) — unlike global
``form_index``, ``resolution_review`` is per-document via ``mention_id``.

Skips cleanly when no safe test database is reachable, like every DB-backed suite.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

from _mutations_common import _aconn_as

from doc_ray.adapters import persistence_canonical_read as canonical_read
from doc_ray.domain.ulid import new_ulid_str

_ROOT = Path(__file__).resolve().parents[3]
_ADAPTERS = _ROOT / "src" / "doc_ray" / "adapters"
_MIGRATION_102 = _ROOT / "db" / "migrations" / "102_document_ownership.sql"

#: The read adapters that run under the request role (docray_api → docray_client).
#: Every table they SELECT must be granted to docray_client.
_CLIENT_READ_ADAPTERS = (
    "persistence_read.py",
    "persistence_read_collections.py",
    "persistence_read_structure.py",
    "persistence_canonical_read.py",
    "persistence_layers_read.py",
    "persistence_visual_read.py",
    "security_audit_read.py",
)

_FROM_JOIN = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


def _tables_referenced(real_tables: set[str]) -> set[str]:
    """Return the real public tables the client-facing read adapters reference.

    Filtering the FROM/JOIN identifiers against the live table set drops CTE names
    and aliases, so only genuine tables are checked.
    """
    referenced: set[str] = set()
    for name in _CLIENT_READ_ADAPTERS:
        for ident in _FROM_JOIN.findall((_ADAPTERS / name).read_text(encoding="utf-8")):
            if ident.lower() in real_tables:
                referenced.add(ident.lower())
    return referenced


def _real_tables(conn) -> set[str]:
    return {
        row["relname"]
        for row in conn.execute(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r'"
        ).fetchall()
    }


def _can_select(conn, role: str, table: str) -> bool:
    return conn.execute(
        "SELECT has_table_privilege(%s, %s, 'SELECT') AS ok", (role, table)
    ).fetchone()["ok"]


def test_docray_client_can_select_every_table_client_read_code_touches(pg) -> None:
    """The class-closing invariant: no client-read table may be un-granted (#15)."""
    conn, _ = pg
    referenced = _tables_referenced(_real_tables(conn))
    assert "resolution_review" in referenced, "guard fixture: the sweep must see the read path"
    missing = sorted(t for t in referenced if not _can_select(conn, "docray_client", t))
    assert not missing, (
        "docray_client lacks SELECT on tables its read adapters SELECT — a "
        f"permission_denied waiting to happen: {missing}"
    )


def test_docray_client_and_api_hold_select_on_resolution_review(pg) -> None:
    conn, _ = pg
    assert _can_select(conn, "docray_client", "resolution_review")
    assert _can_select(conn, "docray_api", "resolution_review")


# --- behavioural: readable AND RLS-scoped under the client role -------------


def _reader(conn, label: str) -> str:
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"{label}-{uuid.uuid4()}",),
    ).fetchone()
    principal_id = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'reader' ON CONFLICT DO NOTHING",
        (principal_id,),
    )
    return principal_id


def _seed_review_row(conn, doc_id: str) -> str:
    """Attach an entity mention to *doc_id* and queue a resolution_review row."""
    import _itdb

    ann = _itdb.make_annotation(conn, doc_id, "entity", "spacy/test")
    span = _itdb.span_of(conn, doc_id, 0, 5)
    mention_id = new_ulid_str()
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s, %s, %s, 'PERSON', 'Alice')",
        (mention_id, ann, span),
    )
    review_id = new_ulid_str()
    conn.execute(
        "INSERT INTO resolution_review "
        "  (id, mention_id, candidates, proposed_canonical_id, confidence, status, reason) "
        "VALUES (%s, %s, '[]'::jsonb, NULL, 0.4, 'pending', 'itest')",
        (review_id, mention_id),
    )
    return review_id


def test_resolution_review_readable_under_client_role_via_adapter(pg) -> None:
    """The owner reads their review queue through the real adapter; a stranger sees none."""
    import _itdb

    conn, created = pg
    conn.execute(_MIGRATION_102.read_text(encoding="utf-8"))
    doc_id = _itdb.make_doc(conn, "Alice's document under review.")
    created.append(doc_id)
    alice = _reader(conn, "rr-alice")
    bob = _reader(conn, "rr-bob")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (alice, doc_id))
    review_id = _seed_review_row(conn, doc_id)

    async def as_(principal_id: str) -> list[dict]:
        aconn = await _aconn_as(principal_id)
        try:
            return await canonical_read.list_resolution_review(aconn, limit=50)
        finally:
            await aconn.close()

    try:
        alice_rows = asyncio.run(as_(alice))
        bob_rows = asyncio.run(as_(bob))
        assert any(str(r["id"]) == review_id for r in alice_rows), "owner reads their review row"
        assert all(str(r["id"]) != review_id for r in bob_rows), "a stranger must not see it"
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))


def test_resolution_review_rls_scopes_a_direct_select(pg) -> None:
    """A direct SELECT (no entity_mention join) must still be RLS-scoped, not just granted.

    This is the test that mandates RLS over a bare grant: without a row policy a
    client could read every tenant's review queue directly.
    """
    import _itdb

    conn, created = pg
    conn.execute(_MIGRATION_102.read_text(encoding="utf-8"))
    doc_id = _itdb.make_doc(conn, "Alice's document, direct-select scope.")
    created.append(doc_id)
    alice = _reader(conn, "rr-d-alice")
    bob = _reader(conn, "rr-d-bob")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (alice, doc_id))
    review_id = _seed_review_row(conn, doc_id)

    def visible_as(principal_id: str) -> int:
        conn.execute("SET ROLE docray_api")
        try:
            conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
            return conn.execute(
                "SELECT count(*) AS n FROM resolution_review WHERE id = %s", (review_id,)
            ).fetchone()["n"]
        finally:
            conn.execute("SELECT set_config('app.principal_id', '', false)")
            conn.execute("RESET ROLE")

    try:
        assert visible_as(alice) == 1, "the owner reads their own review row directly"
        assert visible_as(bob) == 0, "RLS scopes a direct select — no cross-tenant leak"
    finally:
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))
