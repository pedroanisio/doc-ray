"""GraphQL sharing mutations: shareDocument / revokeDocumentShare + documentShares.

Exercises the resolvers through the real schema against Postgres, with each
connection scoped to docray_api + a principal so RLS actually applies (the
document_acl policies from migration 108 are the authoritative backstop; these
tests prove the resolver honours them and audits the outcome).
"""

from __future__ import annotations

import asyncio
import uuid

from _mutations_common import _aconn_as, _make_principal

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal


def _member_with_email(conn, label: str) -> tuple[str, str]:
    """Create a member principal with a local credential; return (id, email)."""
    email = f"{label}-{uuid.uuid4().hex[:10]}@example.com"
    pid = _make_principal(conn, display_name=f"{label}-{uuid.uuid4()}")
    conn.execute(
        "INSERT INTO principal_credentials "
        "(principal_id, tenant_id, email_normalized, password_hash, password_algo, "
        " created_at, updated_at) "
        "SELECT %s, tenant_id, %s, '$argon2id$x', 'argon2id', now(), now() "
        "  FROM principals WHERE id = %s",
        (pid, email, pid),
    )
    return str(pid), email


def _ctx_for(principal_id: str, conn) -> RequestContext:
    return RequestContext(
        conn=conn,
        principal=Principal(
            subject="test", principal_id=principal_id, scopes=frozenset({"read", "write"})
        ),
        loaders=Loaders(conn),
    )


def test_owner_shares_a_document_by_email_and_it_is_listed(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Owner shares this.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    colleague, email = _member_with_email(conn, "colleague")
    schema = build_schema()

    async def check():
        oc = await _aconn_as(str(owner))
        try:
            ctx = _ctx_for(str(owner), oc)
            result = await schema.execute(
                "mutation($d: ID!, $e: String!) { "
                'shareDocument(documentId: $d, email: $e, permission: "read") '
                "{ email permission } }",
                variable_values={"d": doc_id, "e": email},
                context_value=ctx,
            )
            assert result.errors is None, result.errors
            assert result.data["shareDocument"]["permission"] == "read"
            assert result.data["shareDocument"]["email"] == email
            listed = await schema.execute(
                "query($d: ID!) { documentShares(id: $d) { email permission } }",
                variable_values={"d": doc_id},
                context_value=ctx,
            )
            assert listed.errors is None, listed.errors
            assert {s["email"] for s in listed.data["documentShares"]} == {email}
        finally:
            await oc.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
    conn.execute("RESET ROLE")


def test_non_owner_cannot_share(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Not the attacker's to share.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"real-owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    attacker = _make_principal(conn, display_name=f"attacker-{uuid.uuid4()}")
    _victim, victim_email = _member_with_email(conn, "victim")
    schema = build_schema()

    async def check():
        ac = await _aconn_as(str(attacker))
        try:
            result = await schema.execute(
                "mutation($d: ID!, $e: String!) { shareDocument(documentId: $d, email: $e) "
                "{ permission } }",
                variable_values={"d": doc_id, "e": victim_email},
                context_value=_ctx_for(str(attacker), ac),
            )
            assert result.errors is not None
            assert "share" in str(result.errors[0]).lower()
        finally:
            await ac.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id = %s", (owner,))
    conn.execute("DELETE FROM principals WHERE id = %s", (attacker,))
    conn.execute("RESET ROLE")


def test_admin_and_redact_are_not_shareable(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "No escalation via sharing.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    _c, email = _member_with_email(conn, "colleague")
    schema = build_schema()

    async def check():
        oc = await _aconn_as(str(owner))
        try:
            for forbidden in ("admin", "redact", "delete"):
                result = await schema.execute(
                    "mutation($d: ID!, $e: String!, $p: String!) { "
                    "shareDocument(documentId: $d, email: $e, permission: $p) { permission } }",
                    variable_values={"d": doc_id, "e": email, "p": forbidden},
                    context_value=_ctx_for(str(owner), oc),
                )
                assert result.errors is not None, f"{forbidden} must be rejected"
                assert (
                    "shareable" in str(result.errors[0]).lower()
                    or "permission" in str(result.errors[0]).lower()
                )
        finally:
            await oc.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id = %s", (owner,))
    conn.execute("RESET ROLE")


def test_revoke_removes_the_share(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Revoke this share.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    colleague, email = _member_with_email(conn, "colleague")
    schema = build_schema()

    async def check():
        oc = await _aconn_as(str(owner))
        try:
            ctx = _ctx_for(str(owner), oc)
            await schema.execute(
                "mutation($d: ID!, $e: String!) { shareDocument(documentId: $d, email: $e) "
                "{ permission } }",
                variable_values={"d": doc_id, "e": email},
                context_value=ctx,
            )
            revoked = await schema.execute(
                "mutation($d: ID!, $e: String!) { revokeDocumentShare(documentId: $d, email: $e) }",
                variable_values={"d": doc_id, "e": email},
                context_value=ctx,
            )
            assert revoked.errors is None, revoked.errors
            assert revoked.data["revokeDocumentShare"] == 1
            listed = await schema.execute(
                "query($d: ID!) { documentShares(id: $d) { email } }",
                variable_values={"d": doc_id},
                context_value=ctx,
            )
            assert listed.data["documentShares"] == []
        finally:
            await oc.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
    conn.execute("RESET ROLE")


def test_share_to_an_unknown_email_is_rejected(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Share to nobody.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    schema = build_schema()

    async def check():
        oc = await _aconn_as(str(owner))
        try:
            result = await schema.execute(
                'mutation($d: ID!) { shareDocument(documentId: $d, email: "ghost@example.com") '
                "{ permission } }",
                variable_values={"d": doc_id},
                context_value=_ctx_for(str(owner), oc),
            )
            assert result.errors is not None
            assert "no account" in str(result.errors[0]).lower()
        finally:
            await oc.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id = %s", (owner,))
    conn.execute("RESET ROLE")


def test_share_emits_an_audit_event(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Audited share.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    colleague, email = _member_with_email(conn, "colleague")
    schema = build_schema()

    async def check():
        oc = await _aconn_as(str(owner))
        try:
            await schema.execute(
                "mutation($d: ID!, $e: String!) { shareDocument(documentId: $d, email: $e) "
                "{ permission } }",
                variable_values={"d": doc_id, "e": email},
                context_value=_ctx_for(str(owner), oc),
            )
        finally:
            await oc.close()

    asyncio.run(check())
    row = conn.execute(
        "SELECT count(*) AS n FROM audit_events "
        "WHERE action = 'mutation.share_document' AND target_id = %s AND outcome = 'success'",
        (doc_id,),
    ).fetchone()
    assert row["n"] >= 1, "sharing must be recorded in the audit log"
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, colleague))
    conn.execute("RESET ROLE")


def test_owner_publishes_a_document_to_the_org(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Publish to the org.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    schema = build_schema()

    async def check():
        oc = await _aconn_as(str(owner))
        try:
            result = await schema.execute(
                'mutation($d: ID!) { setDocumentVisibility(documentId: $d, visibility: "org") }',
                variable_values={"d": doc_id},
                context_value=_ctx_for(str(owner), oc),
            )
            assert result.errors is None, result.errors
            assert result.data["setDocumentVisibility"] == "org"
            doc = await schema.execute(
                "query($d: ID!) { document(id: $d) { visibility } }",
                variable_values={"d": doc_id},
                context_value=_ctx_for(str(owner), oc),
            )
            assert doc.data["document"]["visibility"] == "org"
        finally:
            await oc.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id = %s", (owner,))
    conn.execute("RESET ROLE")


def test_non_owner_cannot_publish_to_the_org(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Not the stranger's to publish.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    stranger = _make_principal(conn, display_name=f"stranger-{uuid.uuid4()}")
    schema = build_schema()

    async def check():
        sc = await _aconn_as(str(stranger))
        try:
            result = await schema.execute(
                'mutation($d: ID!) { setDocumentVisibility(documentId: $d, visibility: "org") }',
                variable_values={"d": doc_id},
                context_value=_ctx_for(str(stranger), sc),
            )
            assert result.errors is not None
        finally:
            await sc.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (owner, stranger))
    conn.execute("RESET ROLE")


def test_invalid_visibility_is_rejected(pg) -> None:
    import _itdb

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Bad visibility value.")
    created.append(doc_id)
    owner = _make_principal(conn, display_name=f"owner-{uuid.uuid4()}")
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner, doc_id))
    schema = build_schema()

    async def check():
        oc = await _aconn_as(str(owner))
        try:
            result = await schema.execute(
                'mutation($d: ID!) { setDocumentVisibility(documentId: $d, visibility: "public") }',
                variable_values={"d": doc_id},
                context_value=_ctx_for(str(owner), oc),
            )
            assert result.errors is not None
            assert "visibility" in str(result.errors[0]).lower()
        finally:
            await oc.close()

    asyncio.run(check())
    conn.execute("SET ROLE docray_batch")
    conn.execute("DELETE FROM principals WHERE id = %s", (owner,))
    conn.execute("RESET ROLE")
