"""BL-205 guard: the `embedding` read policy must not scan the span fact table.

`semantic_search_sentences` timed out on EVERY corpus-wide query while working
fine when scoped to one `document_id`. The cause was the shape of the row-level
security policy on `embedding`, not the vector index:

    span_id IN (SELECT spans.id FROM spans
                 WHERE spans.document_id IN (SELECT docray_readable_documents()))

Measured on a large corpus with EXPLAIN as `docray_api` with a principal, the
planner could not hash that IN-list — its estimate (tens of millions of span
ids, a quarter of a >100M-row `spans` table) blows past `work_mem` — so it
built a `Materialize` node and scanned it LINEARLY for every candidate row the
HNSW index produced. The same plan hashed the equivalent predicate on
`sentences` (`document_id IN (...)`, reported as `hashed SubPlan`) because that
table carries `document_id` directly and needs no detour through `spans`.

The fix keeps the policy's MEANING identical — `spans.id` is the primary key, so
`span_id IN (SELECT id FROM spans WHERE ...)` and `EXISTS (SELECT 1 FROM spans
WHERE id = span_id AND ...)` select exactly the same rows — while giving the
planner a shape it can execute: one primary-key probe per candidate, against a
readable-document set materialised ONCE as an InitPlan.

These are static guards on the migration and the read path. The behavioural
proof that the policy still hides what it must lives in
``src/doc_ray/tests/test_semantic_search_rls.py``, which needs the isolated test
database like every other DB-backed suite.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _ROOT / "db" / "migrations"
_READER = _ROOT / "src" / "doc_ray" / "adapters" / "persistence_semantic_read.py"


def _policy_migration() -> Path:
    """The migration that redefines the embedding read policy."""
    matches = [
        path
        for path in sorted(_MIGRATIONS.glob("*.sql"))
        if re.search(r"POLICY\s+p_read\s+ON\s+embedding", path.read_text(encoding="utf-8"), re.I)
    ]
    assert matches, "no migration redefines the p_read policy on embedding"
    return matches[-1]


def test_the_read_policy_no_longer_scans_spans_as_an_in_list() -> None:
    """The exact shape that produced a 28M-row Materialize per candidate row."""
    sql = _policy_migration().read_text(encoding="utf-8")
    body = sql[sql.upper().find("CREATE POLICY") :]
    assert not re.search(r"span_id\s+IN\s*\(\s*SELECT", body, re.I), (
        "the policy must not select a set of span ids: that list cannot be hashed "
        "at this cardinality and is scanned linearly for every candidate row"
    )


def test_the_read_policy_probes_spans_by_primary_key() -> None:
    """The correlation on the spans primary key is what makes it one index probe."""
    sql = _policy_migration().read_text(encoding="utf-8")
    assert re.search(r"\bsp\.id\s*=\s*embedding\.span_id\b", sql, re.I), (
        "the subquery must correlate on the spans primary key, or it is just the "
        "old scan wearing different syntax"
    )


def test_the_policy_uses_a_scalar_subquery_not_exists() -> None:
    """EXISTS is not sufficient here, and that is not obvious.

    Rewriting to EXISTS still timed out (>90 s). The planner un-nests a
    correlated EXISTS back into a semi-join and re-derives the whole readable
    span set to hash it:

        Filter: (ANY (span_id = (hashed SubPlan 6).col1))
        SubPlan 6 -> Index Scan on spans (document_id = ANY (InitPlan 4))

    Hashing ~28M span ids is cheaper than materialising them, but it is still
    O(corpus) work on every query. A correlated SCALAR subquery cannot be
    flattened into a semi-join — it must be evaluated per outer row — which is
    exactly the per-row primary-key probe this needs. Measured: 0.20 s.
    """
    sql = _policy_migration().read_text(encoding="utf-8")
    body = sql[sql.upper().find("CREATE POLICY") :]
    assert re.search(
        r"\(\s*SELECT\s+sp\.document_id\s+FROM\s+spans\s+sp\s+WHERE\s+sp\.id\s*=", body, re.I
    ), "the policy must read the owning document through a correlated scalar subquery"
    assert not re.search(r"\bEXISTS\s*\(", body, re.I), (
        "EXISTS lets the planner un-nest into a semi-join over the whole readable "
        "span set, which is the >90s plan this migration exists to replace"
    )


def test_the_readable_document_set_is_materialised_once() -> None:
    """A per-row call to docray_readable_documents() is the second trap.

    Rewriting to EXISTS without this measured 13.0s on the same corpus, because
    the planner re-executed the function once per candidate (`ProjectSet ...
    loops=20419`). Wrapping it in ARRAY(...) makes it an InitPlan, evaluated once
    per query.
    """
    sql = _policy_migration().read_text(encoding="utf-8")
    assert re.search(r"ANY\s*\(\s*ARRAY\s*\(\s*SELECT", sql, re.I), (
        "the readable-document set must be built with ARRAY(SELECT ...) so it "
        "becomes a once-evaluated InitPlan rather than a per-row function call"
    )


def test_the_policy_still_restricts_to_readable_documents() -> None:
    """Performance work must not widen what the policy admits."""
    sql = _policy_migration().read_text(encoding="utf-8")
    assert "docray_readable_documents" in sql, (
        "the rewritten policy must still gate on the caller's readable documents"
    )
    assert re.search(r"sp\.document_id", sql, re.I), (
        "visibility is still decided by the owning document, not by the span alone"
    )


def test_the_migration_does_not_rewrite_the_embedding_table() -> None:
    """`embedding` carries the HNSW index; a full rewrite is not an acceptable fix.

    Adding a denormalised document_id column would also have removed the detour
    through `spans`, but it means UPDATEing every one of ~3.4M rows in an
    HNSW-indexed table — the write amplification application/semantic_embed.py
    exists to avoid ("COPY, never per-row INSERT"). The policy rewrite achieves
    the same read plan with no table rewrite at all.
    """
    sql = _policy_migration().read_text(encoding="utf-8")
    assert not re.search(r"ALTER\s+TABLE\s+embedding\s+ADD\s+COLUMN", sql, re.I), (
        "the fix must not add a column to the HNSW-indexed embedding table"
    )
    assert not re.search(r"UPDATE\s+embedding\b", sql, re.I), (
        "the fix must not rewrite rows in the HNSW-indexed embedding table"
    )


def test_the_reader_did_not_grow_its_own_acl_logic() -> None:
    """Visibility stays in the policy. An app-side document filter would be a
    second, drifting copy of the ACL — and would not remove the policy's cost,
    because RLS is applied regardless of what the query says."""
    text = _READER.read_text(encoding="utf-8")
    assert "docray_readable_documents" not in text, (
        "the reader must not inline the ACL; RLS still applies on top of it"
    )
    assert "hnsw.iterative_scan" in text, (
        "the iterative scan stays: it is what keeps a filtered top-N from "
        "under-returning for a non-admin principal"
    )
