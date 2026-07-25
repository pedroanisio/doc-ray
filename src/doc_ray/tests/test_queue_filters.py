"""Server-side filtering for the jobs list (admin-console pagination + filter).

The admin console's Jobs data table filters by ``status`` and ``kind`` and pages
with the keyset ``after`` cursor. Those filters must be applied in SQL — filtering
a single fetched page client-side would silently drop matches that fall on later
pages. This exercises :func:`queue_postgres.list_async` against the real jobs
table: status/kind filters compose with each other and with keyset pagination,
and the cursor still walks the *filtered* set. Skips without a DB.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest
from psycopg.rows import dict_row

from doc_ray.adapters import queue_postgres as q

_itdb = pytest.importorskip("_itdb")


@pytest.fixture
def qconn():
    conn = _itdb.connect()
    created: list[str] = []
    try:
        yield conn, created
    finally:
        for jid in created:
            conn.execute("DELETE FROM jobs WHERE id = %s", (jid,))
        conn.close()


async def _alist(**kwargs):
    aconn = await psycopg.AsyncConnection.connect(
        _itdb.DB_URL, row_factory=dict_row, autocommit=True
    )
    try:
        return await q.list_async(aconn, **kwargs)
    finally:
        await aconn.close()


def _seed(conn, created):
    """Three queued + one failed across two kinds; return their ids."""
    ids = {
        "ingest_q": q.enqueue(conn, "ingest", {}),
        "annotate_q": q.enqueue(conn, "annotate", {}),
        "annotate_q2": q.enqueue(conn, "annotate", {}),
        "backup_q": q.enqueue(conn, "backup", {}),
    }
    created.extend(ids.values())
    # move one to 'failed' so status filtering has something to select and exclude
    conn.execute("UPDATE jobs SET status = 'failed' WHERE id = %s", (ids["backup_q"],))
    return ids


def test_status_filter_selects_only_matching(qconn):
    conn, created = qconn
    ids = _seed(conn, created)
    rows = asyncio.run(_alist(status="failed", limit=100))
    got = {r["id"] for r in rows if r["id"] in set(ids.values())}
    assert got == {ids["backup_q"]}


def test_kind_filter_selects_only_matching(qconn):
    conn, created = qconn
    ids = _seed(conn, created)
    rows = asyncio.run(_alist(kind="annotate", limit=100))
    got = {r["id"] for r in rows if r["id"] in set(ids.values())}
    assert got == {ids["annotate_q"], ids["annotate_q2"]}


def test_status_and_kind_compose(qconn):
    conn, created = qconn
    ids = _seed(conn, created)
    rows = asyncio.run(_alist(status="queued", kind="annotate", limit=100))
    got = {r["id"] for r in rows if r["id"] in set(ids.values())}
    assert got == {ids["annotate_q"], ids["annotate_q2"]}  # the failed backup excluded by both


def test_cursor_walks_the_filtered_set(qconn):
    conn, created = qconn
    ids = _seed(conn, created)
    # page size 1 over the two queued annotates; the cursor must continue within kind='annotate'
    page1 = asyncio.run(_alist(kind="annotate", limit=1))
    assert len(page1) == 1
    page2 = asyncio.run(_alist(kind="annotate", limit=1, after=page1[0]["cursor"]))
    assert len(page2) == 1
    assert page1[0]["id"] != page2[0]["id"]
    assert {page1[0]["id"], page2[0]["id"]} == {ids["annotate_q"], ids["annotate_q2"]}


def test_no_filters_is_unchanged_behavior(qconn):
    conn, created = qconn
    ids = _seed(conn, created)
    rows = asyncio.run(_alist(limit=100))
    got = {r["id"] for r in rows if r["id"] in set(ids.values())}
    assert got == set(ids.values())  # all four returned, newest-first
