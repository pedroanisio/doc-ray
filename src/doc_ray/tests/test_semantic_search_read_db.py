"""DB integration: semantic sentence retrieval — ranking + RLS isolation.

Runs against the isolated test database with a deterministic fake encoder. Seeds
two documents owned by two different (non-admin) principals, embeds both, then
reads through the async adapter bound to each principal. Proves (1) a principal
retrieves and ranks their own sentence, and (2) RLS excludes the other
principal's sentence even when it is the closest match to the query — the
load-bearing no-cross-principal-leak guarantee.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

import _itdb
import psycopg
from psycopg.rows import dict_row

from doc_ray.adapters import persistence_semantic_read as sread
from doc_ray.application.semantic_embed import embed_document_sentences, to_vector_literal

MODEL = "doc-ray/sbert-mpnet-768"
_ALICE_SENTENCE = "Photosynthesis converts sunlight into chemical energy."
_BOB_SENTENCE = "The quarterly revenue exceeded all forecasts."


class _FakeTextEncoder:
    model_id = MODEL
    dim = 768

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.blake2b(text.encode("utf-8"), digest_size=32).digest()
            vectors.append([((digest[i % 32] + i) % 255) / 255.0 for i in range(self.dim)])
        return vectors


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


async def _search_as(principal_id: str, query_vector: str, *, limit: int = 25) -> list[dict]:
    """Run the async reader on a connection bound to *principal_id* (RLS active)."""
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    try:
        await aconn.execute("SET ROLE docray_api")
        await aconn.execute("SELECT set_config('app.principal_id', %s, false)", (principal_id,))
        return await sread.semantic_search_sentences(aconn, query_vector, MODEL, limit=limit)
    finally:
        await aconn.execute("SELECT set_config('app.principal_id', '', false)")
        await aconn.execute("RESET ROLE")
        await aconn.close()


def test_semantic_search_ranks_own_sentence_and_is_rls_scoped(pg: Any) -> None:
    conn, created = pg
    encoder = _FakeTextEncoder()
    alice = _make_member(conn, f"alice-{uuid.uuid4()}")
    bob = _make_member(conn, f"bob-{uuid.uuid4()}")
    doc_a = _make_owned_doc(conn, _ALICE_SENTENCE, alice)
    doc_b = _make_owned_doc(conn, _BOB_SENTENCE, bob)
    created.extend([doc_a, doc_b])
    embed_document_sentences(conn, doc_a, encoder=encoder)
    embed_document_sentences(conn, doc_b, encoder=encoder)

    # The query vector is exactly Alice's sentence — the closest match in the corpus.
    query_vector = to_vector_literal(encoder.embed_texts([_ALICE_SENTENCE])[0])

    try:
        alice_hits = asyncio.run(_search_as(alice, query_vector))
        assert alice_hits, "Alice must retrieve her own sentence"
        assert alice_hits[0]["text"] == _ALICE_SENTENCE  # exact match ranks first
        assert alice_hits[0]["similarity"] > 0.999
        assert all(str(h["document_id"]) == doc_a for h in alice_hits), "RLS: Alice sees only hers"

        # Bob issues the SAME query (closest to Alice's sentence). RLS must still
        # exclude Alice's document entirely — the no-cross-principal-leak guarantee.
        bob_hits = asyncio.run(_search_as(bob, query_vector))
        assert all(str(h["document_id"]) == doc_b for h in bob_hits)
        assert all(str(h["document_id"]) != doc_a for h in bob_hits)
    finally:
        conn.execute(
            "UPDATE documents SET owner_principal_id = NULL WHERE id IN (%s, %s)", (doc_a, doc_b)
        )
        conn.execute("DELETE FROM principals WHERE id IN (%s, %s)", (alice, bob))
