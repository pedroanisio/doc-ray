"""DB integration: semantic briefing retrieval — RLS isolation + real fallback (#33).

Runs against the isolated test database with a deterministic fake encoder (no model
weights). Seeds keyword-discoverable documents owned by two principals, embeds
their sentences, and drives the shared briefing service through the GraphQL reader
on an RLS-bound connection. Proves: (1) semantic passage ranking actually runs
end to end, (2) each principal's pack contains only their own passages — the
no-cross-principal-leak guarantee — and (3) an un-embedded corpus degrades to the
deterministic lexical fallback, reported in ``retrieval`` and ``gaps``.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

import _itdb
import psycopg
import pytest
from psycopg.rows import dict_row

from doc_ray.application.briefing import build_briefing_pack
from doc_ray.application.semantic_embed import embed_document_sentences
from doc_ray.entrypoints.api import semantic_query
from doc_ray.entrypoints.api.briefing_query import _GraphQLBriefingReader

# A model name unique to this test module. The semantic read filters embeddings by
# model, so a unique name isolates these vectors from every other test's rows in the
# shared `embedding` table — the HNSW candidate set is bounded, so a common model
# name lets an unrelated test's vectors crowd out a per-document match under a full
# suite. The query encoder and the seeding encoder both report this id, so they match.
MODEL = "test-brief-semantic/v1"
_ALICE = "Photosynthesis converts sunlight into chemical energy in chloroplasts."
_BOB = "Photosynthesis is unrelated to the quarterly revenue in this report."

_ROOT = Path(__file__).resolve().parents[3]
_ORG_CORPUS_MIGRATION = _ROOT / "db" / "migrations" / "110_org_corpus.sql"


def _restore_readable_documents(conn: Any) -> None:
    """Re-apply the org-corpus migration so this test is self-contained.

    Sibling RLS suites (test_rbac, test_abac) re-run migrations 061/064/076
    inline against the shared test DB, which re-creates
    ``docray_readable_documents()`` from its pre-ownership definition — admin +
    per-document ACL only — and drops the owner and org-visibility branches
    (102, 110). A member briefing must see the member's own documents, so restore
    the current definition first. This is the idempotent guard
    test_document_ownership already uses for migration 102, targeted at 110 so the
    shared function is left in its HEAD-schema state. CREATE OR REPLACE by the
    superuser that runs migrations keeps the helper BYPASSRLS-owned, so no
    recursion (migration 114).
    """
    conn.execute(_ORG_CORPUS_MIGRATION.read_text(encoding="utf-8"))


class _FakeTextEncoder:
    model_id = MODEL
    dim = 768

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.blake2b(text.encode("utf-8"), digest_size=32).digest()
            out.append([((digest[i % 32] + i) % 255) / 255.0 for i in range(self.dim)])
        return out


@pytest.fixture(autouse=True)
def _fake_encoder(monkeypatch):
    # The briefing GraphQL reader embeds the topic through this cached factory;
    # swap the heavy sentence-transformers stack for a deterministic fake.
    monkeypatch.setattr(semantic_query, "_query_text_encoder", lambda: _FakeTextEncoder())
    yield


def _make_member(conn: Any, name: str) -> str:
    row = conn.execute(
        "INSERT INTO principals (kind, display_name, tenant_id) "
        "VALUES ('human', %s, docray_default_tenant_id()) RETURNING id",
        (name,),
    ).fetchone()
    pid = str(row["id"])
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) "
        "SELECT %s, id FROM roles WHERE name = 'member' ON CONFLICT DO NOTHING",
        (pid,),
    )
    return pid


def _make_owned_doc(conn: Any, text: str, owner_id: str) -> str:
    doc_id = _itdb.make_doc(conn, text)
    conn.execute("UPDATE documents SET owner_principal_id = %s WHERE id = %s", (owner_id, doc_id))
    return doc_id


async def _briefing_as(principal_id: str, topic: str) -> dict[str, Any]:
    """Build a briefing on an RLS-bound connection for *principal_id*."""
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    try:
        await aconn.execute("SET ROLE docray_api")
        await aconn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        return await build_briefing_pack(
            topic, reader=_GraphQLBriefingReader(aconn), max_documents=6, passages_per_document=4
        )
    finally:
        await aconn.execute("SELECT set_config('app.principal_id', '', false)")
        await aconn.execute("RESET ROLE")
        await aconn.close()


def test_semantic_briefing_ranks_passages_and_is_rls_scoped(pg: Any) -> None:
    conn, created = pg
    _restore_readable_documents(conn)
    encoder = _FakeTextEncoder()
    alice = _make_member(conn, f"alice-{uuid.uuid4()}")
    bob = _make_member(conn, f"bob-{uuid.uuid4()}")
    doc_a = _make_owned_doc(conn, _ALICE, alice)
    doc_b = _make_owned_doc(conn, _BOB, bob)
    created.extend([doc_a, doc_b])
    embed_document_sentences(conn, doc_a, encoder=encoder)
    embed_document_sentences(conn, doc_b, encoder=encoder)

    try:
        alice_pack = asyncio.run(_briefing_as(alice, "photosynthesis"))
        # Semantic path actually ran (vectors visible), and produced passages.
        assert "semantic" in alice_pack["retrieval"].lower()
        assert alice_pack["key_passages"], "Alice must get semantically-ranked passages"
        # RLS: every source + passage is Alice's document, never Bob's.
        assert {s["document_id"] for s in alice_pack["sources"]} == {doc_a}
        assert all(p["document_id"] == doc_a for p in alice_pack["key_passages"])

        bob_pack = asyncio.run(_briefing_as(bob, "photosynthesis"))
        assert {s["document_id"] for s in bob_pack["sources"]} == {doc_b}
        assert all(p["document_id"] != doc_a for p in bob_pack["key_passages"])
    finally:
        conn.execute(
            "UPDATE documents SET owner_principal_id = NULL WHERE id IN (%s, %s)", (doc_a, doc_b)
        )
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))


def test_briefing_falls_back_to_lexical_when_the_corpus_has_no_vectors(pg: Any) -> None:
    conn, created = pg
    _restore_readable_documents(conn)
    carol = _make_member(conn, f"carol-{uuid.uuid4()}")
    doc_c = _make_owned_doc(conn, _ALICE, carol)  # discoverable, but NOT embedded
    created.append(doc_c)

    try:
        pack = asyncio.run(_briefing_as(carol, "photosynthesis"))
        # No vectors visible -> deterministic lexical fallback, reported both ways.
        assert "full-text" in pack["retrieval"].lower()
        assert any("vector" in gap.lower() for gap in pack["gaps"]), pack["gaps"]
        # Lexical KWIC still grounds the topic (fallback is not an empty pack).
        assert pack["key_passages"]
        assert pack["key_passages"][0]["matched_terms"]
    finally:
        conn.execute("UPDATE documents SET owner_principal_id = NULL WHERE id = %s", (doc_c,))
        conn.execute("DELETE FROM principals WHERE id = %s", (carol,))
