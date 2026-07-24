"""GraphQL corpus-sharding surface tests (corpusManifest + corpusShard)."""

from __future__ import annotations

import asyncio

from _mutations_common import WRITE_PRINCIPAL, _aconn_as, _seed

from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.loaders import Loaders
from doc_ray.entrypoints.api.schema import build_schema


def _ctx(conn) -> RequestContext:
    return RequestContext(conn=conn, principal=WRITE_PRINCIPAL, loaders=Loaders(conn))


def test_corpus_manifest_lists_every_shard_and_counts_sum_to_total(pg):
    _seed(pg)
    _seed(pg)
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            result = await schema.execute(
                """
                query($k: Int!) {
                  corpusManifest(shardCount: $k) {
                    shardCount totalDocuments shards { index documentCount }
                  }
                }
                """,
                variable_values={"k": 4},
                context_value=_ctx(aconn),
            )
            assert result.errors is None, result.errors
            m = result.data["corpusManifest"]
            assert m["shardCount"] == 4
            assert [s["index"] for s in m["shards"]] == [0, 1, 2, 3]
            assert sum(s["documentCount"] for s in m["shards"]) == m["totalDocuments"]
            assert m["totalDocuments"] >= 2  # at least the two we seeded
        finally:
            await aconn.close()

    asyncio.run(check())


def test_corpus_shard_returns_a_disjoint_slice_that_reunions_to_the_manifest_total(pg):
    _seed(pg)
    _seed(pg)
    _seed(pg)
    schema = build_schema()
    k = 3

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            total = (
                await schema.execute(
                    "query($k:Int!){corpusManifest(shardCount:$k){totalDocuments}}",
                    variable_values={"k": k},
                    context_value=_ctx(aconn),
                )
            ).data["corpusManifest"]["totalDocuments"]

            seen: set[str] = set()
            for shard in range(k):
                result = await schema.execute(
                    """
                    query($i: Int!, $k: Int!) {
                      corpusShard(shardIndex: $i, shardCount: $k, limit: 10000) { id }
                    }
                    """,
                    variable_values={"i": shard, "k": k},
                    context_value=_ctx(aconn),
                )
                assert result.errors is None, result.errors
                ids = {d["id"] for d in result.data["corpusShard"]}
                assert not (ids & seen), "shards overlap"
                seen |= ids
            assert len(seen) == total  # the shards partition the whole visible corpus
        finally:
            await aconn.close()

    asyncio.run(check())


def test_corpus_shard_rejects_invalid_arguments(pg):
    schema = build_schema()

    async def check() -> None:
        aconn = await _aconn_as(WRITE_PRINCIPAL.principal_id)
        try:
            for variables, needle in (
                ({"i": 0, "k": 0}, "shardCount must be >= 1"),
                ({"i": 4, "k": 4}, "shardIndex must be in"),
                ({"i": -1, "k": 4}, "shardIndex must be in"),
            ):
                result = await schema.execute(
                    "query($i:Int!,$k:Int!){corpusShard(shardIndex:$i,shardCount:$k){id}}",
                    variable_values=variables,
                    context_value=_ctx(aconn),
                )
                assert result.errors is not None
                assert needle in str(result.errors[0])
            bad_manifest = await schema.execute(
                "query($k:Int!){corpusManifest(shardCount:$k){shardCount}}",
                variable_values={"k": 0},
                context_value=_ctx(aconn),
            )
            assert bad_manifest.errors is not None
            assert "shardCount must be >= 1" in str(bad_manifest.errors[0])
        finally:
            await aconn.close()

    asyncio.run(check())
