"""Reader-role corpus visibility — migration 076 SUPERSEDED by 102.

076 gave any principal holding the ``read`` permission visibility of the whole
tenant corpus (minus ``sensitivity=sensitive`` documents) as a stopgap for the
missing granting flow. On a SaaS with open registration that resolved to "every
registered user reads every document", because one tenant, zero ``document_acl``
rows and zero sensitivity tags left the branch unnarrowed.

Migration 102 replaced that branch with ownership (see
``test_document_ownership.py``). What remains here is the anti-regression: the
blanket role-read branch must never come back, and re-applying 076 in the
shared test DB must not be how a sibling suite resurrects it.

The RLS tests run against the live schema (skipped without the isolated test
DB, like every other DB-backed suite).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_102 = _ROOT / "db" / "migrations" / "102_document_ownership.sql"


def test_migration_077_bumps_schema_version() -> None:
    migration = (_ROOT / "db" / "migrations" / "077_schema_version_1_24_0.sql").read_text(
        encoding="utf-8"
    )
    assert "1.24.0" in migration
    # engineering_decisions.yml declares a source_schema_version; the *current*
    # value moves with every schema bump and is enforced against __version__ by
    # check_version_consistency.py — this test must not pin it to a stale value.
    decisions = (_ROOT / "engineering_decisions.yml").read_text(encoding="utf-8")
    assert re.search(r'^source_schema_version:\s*"\d+\.\d+\.\d+"', decisions, re.MULTILINE)


def test_no_migration_reintroduces_the_blanket_role_read_branch() -> None:
    """076 is the last migration allowed to grant corpus-wide read by role.

    Ordering matters: a later migration re-creating ``docray_readable_documents()``
    with the role-permission branch would silently restore the leak, exactly as
    076 itself did to 061's definition.
    """
    offenders = []
    for path in sorted((_ROOT / "db" / "migrations").glob("*.sql")):
        if path.name <= "102_document_ownership.sql":
            continue
        body = path.read_text(encoding="utf-8")
        if "docray_readable_documents" in body and "p.action = 'read'" in body:
            offenders.append(path.name)
    assert not offenders, f"migrations reintroduce corpus-wide role read: {offenders}"


def _apply_migration_102(conn) -> None:
    conn.execute(_MIGRATION_102.read_text(encoding="utf-8"))


def test_read_permission_alone_no_longer_reveals_the_corpus(pg) -> None:
    """The incident, as a standing regression test.

    A freshly registered reader — ``read`` permission, no ACL row, owning
    nothing — must see an empty corpus, not the tenant's documents.
    """
    import _itdb

    conn, created = pg
    _apply_migration_102(conn)
    doc_id = _itdb.make_doc(conn, "Corpus leak regression probe.")
    created.append(doc_id)
    conn.execute("UPDATE documents SET owner_principal_id = NULL WHERE id = %s", (doc_id,))
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (f"reader-vis-{uuid.uuid4()}",),
    ).fetchone()
    reader = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'reader' ON CONFLICT DO NOTHING",
        (reader,),
    )
    try:
        conn.execute("SET ROLE docray_api")
        conn.execute("SELECT set_config('app.principal_id', %s, false)", (reader,))
        visible = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")
        assert visible is None, "the reader role must not confer corpus-wide read"
    finally:
        conn.execute("SELECT set_config('app.principal_id', '', false)")
        conn.execute("RESET ROLE")
        conn.execute("DELETE FROM principals WHERE id = %s", (reader,))
