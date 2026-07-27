"""Read-only SQL probes shared by the production deployment checker."""

from __future__ import annotations

CORPUS_ROLLUP_HEALTH_SQL = """
WITH document_rollup AS (
    SELECT d.tenant_id,
           count(*)::bigint AS documents,
           COALESCE(SUM(ds.n_sentences), 0)::bigint AS expected_sentences
      FROM documents d
      LEFT JOIN document_stats ds ON ds.document_id = d.id
     GROUP BY d.tenant_id
),
lexeme_rollup AS (
    SELECT tenant_id,
           count(*) FILTER (WHERE uses > 0)::bigint AS indexed_lexemes
      FROM lexeme_stats
     GROUP BY tenant_id
)
SELECT count(*)::bigint AS failing_tenants
  FROM document_rollup d
  LEFT JOIN corpus_stats cs USING (tenant_id)
  LEFT JOIN lexeme_rollup ls USING (tenant_id)
 WHERE cs.tenant_id IS NULL
    OR cs.n_sentences <> d.expected_sentences
    OR (COALESCE(ls.indexed_lexemes, 0) > 0 AND cs.n_distinct_lexemes <= 0)
"""

__all__ = ["CORPUS_ROLLUP_HEALTH_SQL"]
