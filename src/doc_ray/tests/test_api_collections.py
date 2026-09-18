"""GraphQL document-collection surface tests."""

from __future__ import annotations

import asyncio
import json

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.domain.ulid import new_ulid_str
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.security import Principal


def _ctx(conn, principal=WRITE_PRINCIPAL) -> RequestContext:
    return RequestContext(conn=conn, principal=principal, loaders=Loaders(conn))


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{new_ulid_str()}"


def test_collections_query_exposes_manual_and_smart_collections(pg):
    conn, _created = pg
    manual_doc = _seed(pg)
    smart_doc = _seed(pg)
    conn.execute("INSERT INTO tags (tag) VALUES ('api-collection') ON CONFLICT DO NOTHING")
    conn.execute(
        "INSERT INTO document_tags (document_id, tag_id) "
        "SELECT %s, id FROM tags WHERE tag = 'api-collection'",
        (smart_doc,),
    )
    manual_name = _unique_name("manual")
    smart_name = _unique_name("smart")
    manual_id = conn.execute(
        "INSERT INTO document_collections (name, description, kind) "
        "VALUES (%s, 'Manual API set', 'manual') RETURNING id",
        (manual_name,),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s, %s)",
        (manual_id, manual_doc),
    )
    smart_rule = {"tags": ["api-collection"], "status": "extracted"}
    smart_id = conn.execute(
        "INSERT INTO document_collections (name, description, kind, rule) "
        "VALUES (%s, 'Smart API set', 'smart', %s::jsonb) RETURNING id",
        (smart_name, json.dumps(smart_rule)),
    ).fetchone()["id"]
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            result = await schema.execute(
                """
                query($manual: ID!, $smart: ID!) {
                  collections(limit: 200) {
                    id name kind description documentCount ruleSummary
                    documents(limit: 10) { id title }
                  }
                  manual: collection(id: $manual) { id name kind documentCount documents { id } }
                  smart: collection(id: $smart) { id name kind documentCount documents { id } }
                }
                """,
                variable_values={"manual": str(manual_id), "smart": str(smart_id)},
                context_value=_ctx(aconn),
            )
            assert result.errors is None, result.errors
            by_name = {row["name"]: row for row in result.data["collections"]}

            assert by_name[manual_name]["kind"] == "manual"
            assert by_name[manual_name]["documentCount"] == 1
            assert [doc["id"] for doc in by_name[manual_name]["documents"]] == [manual_doc]

            assert by_name[smart_name]["kind"] == "smart"
            assert by_name[smart_name]["documentCount"] == 1
            assert by_name[smart_name]["ruleSummary"] == "status: extracted; tags: api-collection"
            assert [doc["id"] for doc in by_name[smart_name]["documents"]] == [smart_doc]

            assert result.data["manual"]["documents"][0]["id"] == manual_doc
            assert result.data["smart"]["documents"][0]["id"] == smart_doc
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM document_collections WHERE id IN (%s, %s)", (manual_id, smart_id))


def test_document_exposes_collections(pg):
    conn, _created = pg
    doc_id = _seed(pg)
    collection_id = conn.execute(
        "INSERT INTO document_collections (name, description, kind) "
        "VALUES (%s, '', 'manual') RETURNING id",
        (_unique_name("doc-membership"),),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO document_collection_members (collection_id, document_id) VALUES (%s, %s)",
        (collection_id, doc_id),
    )
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            result = await schema.execute(
                "query($id: ID!) { document(id: $id) { id collections { id kind documentCount } } }",
                variable_values={"id": doc_id},
                context_value=_ctx(aconn),
            )
            assert result.errors is None, result.errors
            assert result.data["document"]["collections"] == [
                {"id": str(collection_id), "kind": "manual", "documentCount": 1}
            ]
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM document_collections WHERE id = %s", (collection_id,))


def test_collection_mutations_create_add_and_remove_manual_membership(pg):
    conn, _created = pg
    doc_id = _seed(pg)
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            created = await schema.execute(
                """
                mutation($name: String!) {
                  createCollection(name: $name, description: "From API", kind: "manual") {
                    id name kind documentCount
                  }
                }
                """,
                variable_values={"name": _unique_name("created")},
                context_value=_ctx(aconn),
            )
            assert created.errors is None, created.errors
            collection_id = created.data["createCollection"]["id"]
            assert created.data["createCollection"]["documentCount"] == 0

            added = await schema.execute(
                """
                mutation($collection: ID!, $doc: ID!) {
                  addDocumentToCollection(collectionId: $collection, documentId: $doc) {
                    id documentCount documents { id }
                  }
                }
                """,
                variable_values={"collection": collection_id, "doc": doc_id},
                context_value=_ctx(aconn),
            )
            assert added.errors is None, added.errors
            assert added.data["addDocumentToCollection"]["documentCount"] == 1
            assert added.data["addDocumentToCollection"]["documents"][0]["id"] == doc_id

            removed = await schema.execute(
                """
                mutation($collection: ID!, $doc: ID!) {
                  removeDocumentFromCollection(collectionId: $collection, documentId: $doc) {
                    id documentCount documents { id }
                  }
                }
                """,
                variable_values={"collection": collection_id, "doc": doc_id},
                context_value=_ctx(aconn),
            )
            assert removed.errors is None, removed.errors
            assert removed.data["removeDocumentFromCollection"]["documentCount"] == 0

            smart = await schema.execute(
                """
                mutation($name: String!) {
                  createCollection(
                    name: $name,
                    description: "Smart from API",
                    kind: "smart",
                    status: "extracted",
                    tags: ["api"]
                  ) {
                    name kind rule ruleSummary documentCount
                  }
                }
                """,
                variable_values={"name": _unique_name("smart-created")},
                context_value=_ctx(aconn),
            )
            assert smart.errors is None, smart.errors
            assert smart.data["createCollection"]["kind"] == "smart"
            assert smart.data["createCollection"]["rule"] == {
                "status": "extracted",
                "tags": ["api"],
            }
            assert smart.data["createCollection"]["ruleSummary"] == "status: extracted; tags: api"
        finally:
            await aconn.close()

    asyncio.run(check())


def test_update_collection_renames_and_edits_description(pg):
    conn, _created = pg
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            created = await schema.execute(
                'mutation($n:String!){createCollection(name:$n,kind:"manual"){id}}',
                variable_values={"n": _unique_name("AI &amp; Business")},
                context_value=_ctx(aconn),
            )
            assert created.errors is None, created.errors
            cid = created.data["createCollection"]["id"]
            new_name = _unique_name("AI & Business")
            renamed = await schema.execute(
                """
                mutation($c: ID!, $n: String, $d: String) {
                  updateCollection(collectionId: $c, name: $n, description: $d) {
                    id name description
                  }
                }
                """,
                variable_values={"c": cid, "n": new_name, "d": "Nonfiction"},
                context_value=_ctx(aconn),
            )
            assert renamed.errors is None, renamed.errors
            assert renamed.data["updateCollection"]["name"] == new_name
            assert renamed.data["updateCollection"]["description"] == "Nonfiction"
        finally:
            await aconn.close()

    asyncio.run(check())


def test_update_collection_rejects_duplicate_name(pg):
    conn, _created = pg
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            taken = _unique_name("taken")
            await schema.execute(
                'mutation($n:String!){createCollection(name:$n,kind:"manual"){id}}',
                variable_values={"n": taken},
                context_value=_ctx(aconn),
            )
            b = await schema.execute(
                'mutation($n:String!){createCollection(name:$n,kind:"manual"){id}}',
                variable_values={"n": _unique_name("other")},
                context_value=_ctx(aconn),
            )
            bid = b.data["createCollection"]["id"]
            clash = await schema.execute(
                "mutation($c:ID!,$n:String){updateCollection(collectionId:$c,name:$n){id}}",
                variable_values={"c": bid, "n": taken},
                context_value=_ctx(aconn),
            )
            assert clash.errors is not None
            assert "already exists" in str(clash.errors[0])
        finally:
            await aconn.close()

    asyncio.run(check())


def test_delete_collection_removes_the_container_but_never_the_documents(pg):
    """Deleting a collection cascades only its membership rows — the books survive."""
    conn, _created = pg
    doc_id = _seed(pg)
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            created = await schema.execute(
                'mutation($n:String!){createCollection(name:$n,kind:"manual"){id}}',
                variable_values={"n": _unique_name("to-delete")},
                context_value=_ctx(aconn),
            )
            cid = created.data["createCollection"]["id"]
            await schema.execute(
                "mutation($c:ID!,$d:ID!){addDocumentToCollection(collectionId:$c,documentId:$d){id}}",
                variable_values={"c": cid, "d": doc_id},
                context_value=_ctx(aconn),
            )
            deleted = await schema.execute(
                "mutation($c:ID!){deleteCollection(collectionId:$c)}",
                variable_values={"c": cid},
                context_value=_ctx(aconn),
            )
            assert deleted.errors is None, deleted.errors
            assert deleted.data["deleteCollection"] is True
        finally:
            await aconn.close()

    asyncio.run(check())
    # The collection and its membership rows are gone; the DOCUMENT is untouched.
    assert (
        conn.execute("SELECT count(*) AS n FROM documents WHERE id=%s", (doc_id,)).fetchone()["n"]
        == 1
    )
    assert (
        conn.execute(
            "SELECT count(*) AS n FROM document_collection_members WHERE document_id=%s", (doc_id,)
        ).fetchone()["n"]
        == 0
    )


def test_delete_collection_rejects_unknown_id(pg):
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            result = await schema.execute(
                "mutation($c:ID!){deleteCollection(collectionId:$c)}",
                variable_values={"c": "00000000-0000-0000-0000-000000000000"},
                context_value=_ctx(aconn),
            )
            assert result.errors is not None
            assert "unknown collection" in str(result.errors[0])
        finally:
            await aconn.close()

    asyncio.run(check())


def test_add_document_to_collection_requires_document_write_grant(pg):
    conn, _created = pg
    doc_id = _seed(pg)
    outsider = conn.execute(
        "INSERT INTO principals (kind, display_name) VALUES ('human', %s) RETURNING id",
        (_unique_name("collection-outsider"),),
    ).fetchone()["id"]
    collection_id = conn.execute(
        "INSERT INTO document_collections (name, description, kind) "
        "VALUES (%s, '', 'manual') RETURNING id",
        (_unique_name("private-manual"),),
    ).fetchone()["id"]
    principal = Principal(
        subject="outsider",
        principal_id=str(outsider),
        scopes=frozenset({"read", "write"}),
    )
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(str(outsider))
        try:
            result = await schema.execute(
                """
                mutation($collection: ID!, $doc: ID!) {
                  addDocumentToCollection(collectionId: $collection, documentId: $doc) { id }
                }
                """,
                variable_values={"collection": str(collection_id), "doc": doc_id},
                context_value=_ctx(aconn, principal),
            )
            assert result.errors is not None
            assert "write grant" in str(result.errors[0]).lower()
        finally:
            await aconn.close()

    try:
        asyncio.run(check())
    finally:
        conn.execute("DELETE FROM document_collections WHERE id = %s", (collection_id,))
        conn.execute("DELETE FROM principals WHERE id = %s", (outsider,))


def test_collection_mutation_rejects_malformed_collection_id_cleanly(pg):
    doc_id = _seed(pg)
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            result = await schema.execute(
                """
                mutation($doc: ID!) {
                  addDocumentToCollection(collectionId: "not-a-uuid", documentId: $doc) { id }
                }
                """,
                variable_values={"doc": doc_id},
                context_value=_ctx(aconn),
            )
            assert result.errors is not None
            message = str(result.errors[0]).lower()
            assert "unknown collection not-a-uuid" in message
            assert "invalid input syntax" not in message
        finally:
            await aconn.close()

    asyncio.run(check())


def test_create_collection_rejects_a_name_too_long_for_its_unique_index(pg):
    """`document_collections.name` is `text NOT NULL UNIQUE` (migration 047).

    Same shape as the authors defect: an oversized value reaches a
    btree that cannot hold it and the write aborts with a database-level error.
    A collection name is typed by a person, though, so the honest response is to
    reject it by name rather than silently store a truncated one they did not
    choose.
    """
    conn, _created = pg
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            result = await schema.execute(
                'mutation($n:String!){createCollection(name:$n,kind:"manual"){id}}',
                variable_values={"n": "c" * 5000},
                context_value=_ctx(aconn),
            )
            assert result.errors is not None
            assert "too long" in str(result.errors[0]).lower()
        finally:
            await aconn.close()

    asyncio.run(check())
