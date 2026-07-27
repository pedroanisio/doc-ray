"""Integration + RLS tests for the term-frequency read adapter (skip without a DB).

The pure verdict logic is covered by test_term_frequency.py. What only a real
database can prove lives here: that the counts are the tenant's actual
``lexeme_stats`` totals, that the whole batch is served by ONE statement, that an
unattested term is a real zero (not an error), and that a caller in another tenant
cannot see the frequencies at all. Seeds with the sync helpers (``_itdb``) and reads
back through the async adapter, the same split as test_read_structure_db.py.

The fixture is sized so all three verdicts fall out of real data: a term at ~2% of
the corpus (``high``), one at ~0.04% (``low``), and one absent entirely (unattested).
"""

from __future__ import annotations

import asyncio

from doc_ray.adapters import persistence as db
from doc_ray.adapters import persistence_read_terms as terms_read
from doc_ray.domain import term_frequency as tf
from doc_ray.tests._mutations_common import _aconn_as

#: A fixed admin principal in the default tenant (the tenant make_doc seeds into).
ADMIN_PID = "00000000-0000-0000-0000-0000000000f6"

_FLOOD = "quxfloodterm"  # unique lemmas, so their counts are exactly what we seed
_RARE = "quxrareterm"
_FILLER = "quxfillerterm"
_MISSING = "quxmissingterm"


def _seed(pg, *, flood: int = 50, rare: int = 1, filler: int = 2449) -> tuple[str, str]:
    """Seed one document's lexeme occurrences and refresh the rollup; return (doc, tenant).

    ``flood``/``rare``/``filler`` are occurrence counts for three distinct lemmas, so
    the caller-tenant total is exactly their sum. Uses unique lemmas that no other
    suite seeds, so a refresh yields those exact counts.
    """
    import _itdb

    conn, created = pg
    _itdb.ensure_admin_principal(conn, ADMIN_PID, "test-term-frequency")
    doc_id = _itdb.make_doc(conn, "Term frequency fixture document.")
    created.append(doc_id)
    annotation_id = _itdb.make_annotation(conn, doc_id, "lexeme", "test")

    total = flood + rare + filler
    pairs = [(i * 2, i * 2 + 1) for i in range(total)]
    spans = db.ensure_spans(conn, doc_id, pairs)
    span_ids = [spans[p] for p in pairs]

    lx_flood, _ = db.get_or_create_lexeme(conn, {}, _FLOOD, "NOUN", "en")
    lx_rare, _ = db.get_or_create_lexeme(conn, {}, _RARE, "NOUN", "en")
    lx_filler, _ = db.get_or_create_lexeme(conn, {}, _FILLER, "NOUN", "en")
    assign = [lx_flood] * flood + [lx_rare] * rare + [lx_filler] * filler

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lexeme_occurrences (annotation_id, span_id, lexeme_uid) VALUES (%s,%s,%s)",
            [(annotation_id, span_id, uid) for span_id, uid in zip(span_ids, assign, strict=True)],
        )
    tenant = conn.execute("SELECT tenant_id FROM documents WHERE id = %s", (doc_id,)).fetchone()[
        "tenant_id"
    ]
    conn.execute("SELECT docray_refresh_lexeme_stats(%s)", (str(tenant),))
    return doc_id, str(tenant)


def test_term_frequencies_report_real_counts_and_every_verdict_from_real_data(pg):
    conn, _ = pg
    doc_id, tenant = _seed(pg)
    keys = [tf.norm_lemma(t) for t in (_FLOOD, _RARE, _MISSING)]

    async def check():
        aconn = await _aconn_as(ADMIN_PID)
        try:
            counts, total_uses = await terms_read.term_frequencies(aconn, keys)
        finally:
            await aconn.close()

        # Exact counts for the unique lemmas; the unattested term is a real zero.
        assert counts[tf.norm_lemma(_FLOOD)] == 50
        assert counts[tf.norm_lemma(_RARE)] == 1
        assert counts[tf.norm_lemma(_MISSING)] == 0

        # The adapter's total is the tenant's actual rollup total (not guessed).
        live_total = conn.execute(
            "SELECT COALESCE(SUM(uses), 0) AS t FROM lexeme_stats WHERE tenant_id = %s",
            (tenant,),
        ).fetchone()["t"]
        assert total_uses == live_total
        assert total_uses >= 2500  # 50 + 1 + 2449, isolated to this tenant

        prepared = tf.prepare_terms([_FLOOD, _RARE, _MISSING])
        flood, rare, missing = tf.build_verdicts(prepared, counts, total_uses)
        # A known-common term is high with the numbers that justify it (share ~2%).
        assert flood.flood_risk == "high" and flood.uses == 50 and flood.attested
        assert flood.note and "rarer" in flood.note.lower()
        # A rare term (share ~0.04%) is low.
        assert rare.flood_risk == "low" and rare.attested
        # The unattested term is distinguishable and says so (FR-6).
        assert missing.flood_risk == "low" and missing.attested is False
        assert "unattested" in (missing.note or "").lower()

        # A phrase reuses those component counts from the same batched read. Its
        # rarest component is a declared upper bound, never an adjacency claim.
        (phrase,) = tf.build_verdicts(tf.prepare_terms([f"{_FLOOD} {_RARE}"]), counts, total_uses)
        assert phrase.measurement == tf.COMPONENT_UPPER_BOUND
        assert phrase.uses == 1 and phrase.attested is None

    asyncio.run(check())


def test_a_ten_term_batch_is_served_by_a_single_sql_statement(pg, monkeypatch):
    # Acceptance: a batch of 10 terms is one SQL statement (not N queries in a loop).
    _seed(pg, flood=5, rare=1, filler=20)
    keys = [f"quxbatch{i}" for i in range(10)]

    statements: list[str] = []
    original = terms_read._rows

    async def _spy(conn, sql, params):
        statements.append(sql)
        return await original(conn, sql, params)

    monkeypatch.setattr(terms_read, "_rows", _spy)

    async def check():
        aconn = await _aconn_as(ADMIN_PID)
        try:
            counts, _total = await terms_read.term_frequencies(aconn, keys)
            assert len(counts) == 10  # one row per key, all unattested here
        finally:
            await aconn.close()

    asyncio.run(check())
    assert len(statements) == 1, f"expected one statement, issued {len(statements)}"


def test_a_caller_in_another_tenant_cannot_see_the_frequencies(pg):
    # RLS: lexeme_stats is tenant-keyed. A caller whose tenant is not the data's
    # tenant (here: anonymous, no tenant) must see zero, never the real counts —
    # an unscoped read would leak 50. Mirrors test_api_lexeme_rls.py.
    _seed(pg)
    keys = [tf.norm_lemma(t) for t in (_FLOOD, _RARE)]

    async def check():
        admin = await _aconn_as(ADMIN_PID)
        anon = await _aconn_as(None)
        try:
            admin_counts, admin_total = await terms_read.term_frequencies(admin, keys)
            anon_counts, anon_total = await terms_read.term_frequencies(anon, keys)
        finally:
            await admin.close()
            await anon.close()

        # The owning tenant sees its vocabulary...
        assert admin_counts[tf.norm_lemma(_FLOOD)] == 50 and admin_total >= 2500
        # ...a caller in no/other tenant sees nothing: the predicate is applied.
        assert anon_counts[tf.norm_lemma(_FLOOD)] == 0
        assert anon_counts[tf.norm_lemma(_RARE)] == 0
        assert anon_total == 0

    asyncio.run(check())
