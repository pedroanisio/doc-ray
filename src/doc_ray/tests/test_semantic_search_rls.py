"""BL-205: the rewritten `embedding` read policy must hide exactly what it hid.

The policy was rewritten for plan shape, not for meaning — `spans.id` is the
primary key, so

    span_id IN (SELECT id FROM spans WHERE document_id IN readable)
    EXISTS (SELECT 1 FROM spans WHERE id = span_id AND document_id IN readable)

select the same rows. That equivalence is the whole safety argument for the
change, and an argument is not a test: a policy rewrite that widens visibility
by one row is a confidentiality bug, and it would be invisible in the latency
numbers that motivated the work.

So these tests assert the boundary directly, on the live schema, from the same
`docray_api` + principal path the API uses. Skipped without the isolated test
database, like every other DB-backed suite.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest


def _as_principal(conn: psycopg.Connection, principal_id: str | None) -> None:
    """Enter the request-time identity the API uses: docray_api + a principal."""
    conn.execute("RESET ROLE")
    conn.execute("SET ROLE docray_api")
    conn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id or "",))


def _as_batch(conn: psycopg.Connection) -> None:
    """The trusted writer identity (BYPASSRLS), used to set fixtures up."""
    conn.execute("RESET ROLE")
    conn.execute("SET ROLE docray_batch")


def _seed_document_with_embedding(conn: psycopg.Connection, created: list[str]) -> tuple[str, str]:
    """Create a document with one sentence, its span, and one embedding row."""
    _as_batch(conn)
    tenant = conn.execute("SELECT docray_current_tenant()").fetchone()[0]
    if tenant is None:
        tenant = conn.execute("SELECT id FROM tenants LIMIT 1").fetchone()[0]
    doc_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO documents (id, title, status, tenant_id) VALUES (%s,%s,'extracted',%s)",
        (doc_id, f"BL-205 fixture {doc_id[:8]}", tenant),
    )
    created.append(doc_id)
    span_id = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,0,5) RETURNING id",
        (doc_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO sentences (document_id, span_id, ordinal, text) VALUES (%s,%s,0,'hello')",
        (doc_id, span_id),
    )
    dims = conn.execute("SELECT vector_dims(vector) FROM embedding LIMIT 1").fetchone()
    width = int(dims[0]) if dims and dims[0] else 768
    vector = "[" + ",".join(["0.001"] * width) + "]"
    conn.execute(
        "INSERT INTO embedding (span_id, model, vector) VALUES (%s,'bl205-test',%s::vector)",
        (span_id, vector),
    )
    conn.commit()
    return doc_id, str(span_id)


def _visible_embeddings(conn: psycopg.Connection, span_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM embedding WHERE span_id = %s AND model = 'bl205-test'",
        (span_id,),
    ).fetchone()[0]


def test_an_unreadable_documents_embedding_stays_hidden(pg) -> None:
    """The confidentiality boundary the rewrite must not move."""
    conn, created = pg
    try:
        _, span_id = _seed_document_with_embedding(conn, created)
        # A principal with no ACL grant on the fixture document.
        _as_principal(conn, str(uuid.uuid4()))
        assert _visible_embeddings(conn, span_id) == 0, (
            "an embedding whose document the caller cannot read must not be "
            "selectable; the rewritten policy has widened visibility"
        )
    finally:
        conn.rollback()
        _as_batch(conn)


def test_a_readable_documents_embedding_is_visible(pg) -> None:
    """The other half: the rewrite must not hide rows it used to return."""
    conn, created = pg
    try:
        doc_id, span_id = _seed_document_with_embedding(conn, created)
        _as_batch(conn)
        principal_id = conn.execute("SELECT id FROM principals LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO document_acl (document_id, principal_id, can_read) VALUES (%s,%s,true) "
            "ON CONFLICT DO NOTHING",
            (doc_id, principal_id),
        )
        conn.commit()
        _as_principal(conn, str(principal_id))
        assert _visible_embeddings(conn, span_id) == 1, (
            "a principal granted read on the document must still see its embedding"
        )
    finally:
        conn.rollback()
        _as_batch(conn)


def test_a_principal_with_no_documents_sees_no_embeddings(pg) -> None:
    """The degenerate case that made the old plan pathological.

    With an empty readable set the HNSW iterative scan exhausts its candidates
    and returns nothing. That must stay correct (and terminate) rather than
    leaking or hanging.
    """
    conn, created = pg
    try:
        _, span_id = _seed_document_with_embedding(conn, created)
        _as_principal(conn, str(uuid.uuid4()))
        assert _visible_embeddings(conn, span_id) == 0
        total = conn.execute("SELECT count(*) FROM embedding").fetchone()[0]
        assert total == 0, (
            f"a principal with no readable documents must see an empty embedding table, saw {total}"
        )
    finally:
        conn.rollback()
        _as_batch(conn)


@pytest.mark.parametrize("model", ["bl205-test"])
def test_policy_is_the_only_thing_hiding_rows(pg, model: str) -> None:
    """Sanity: the fixture row really exists, so the assertions above are not
    passing because nothing was inserted."""
    conn, created = pg
    try:
        _, span_id = _seed_document_with_embedding(conn, created)
        _as_batch(conn)
        assert _visible_embeddings(conn, span_id) == 1, (
            "the trusted writer must see the fixture row; if it does not, the "
            "visibility tests above prove nothing"
        )
    finally:
        conn.rollback()
        _as_batch(conn)
