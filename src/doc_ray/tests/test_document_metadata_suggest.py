"""Verified document-metadata suggestion workflow tests (BL-061)."""

from __future__ import annotations

import _itdb
import pytest

from doc_ray.application.document_metadata_suggest import (
    MetadataSuggestionVerifier,
    generate_suggestions,
)
from doc_ray.application.verification import ModelOutputVerificationError, verify_model_output


def test_metadata_suggestion_verifier_rejects_untrusted_shape():
    verifier = MetadataSuggestionVerifier()

    with pytest.raises(ModelOutputVerificationError):
        verify_model_output(
            {"field": "tags", "suggested_value": ["ok"], "confidence": 1.7, "reason": "x"},
            verifier,
        )


def test_generate_suggestions_persists_verified_title_tags_and_duplicates(pg):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Graph databases make knowledge searchable.")
    created.append(doc_id)
    dup_id = _itdb.make_doc(conn, "Graph databases make knowledge searchable too.")
    created.append(dup_id)
    conn.execute(
        "UPDATE documents SET title='upload', original_filename='graph-database-notes.pdf' "
        "WHERE id=%s",
        (doc_id,),
    )
    conn.execute("UPDATE documents SET title='Graph Database Notes' WHERE id=%s", (dup_id,))

    out = generate_suggestions(conn, doc_id)

    assert out["suggested"] >= 2
    rows = conn.execute(
        "SELECT field, suggested_value, confidence, status, source FROM document_metadata_suggestion "
        "WHERE document_id=%s ORDER BY field, suggested_value",
        (doc_id,),
    ).fetchall()
    fields = {r["field"] for r in rows}
    assert {"title", "tags", "duplicate"}.issubset(fields)
    assert all(r["status"] == "pending" for r in rows)
    assert all(0 <= float(r["confidence"]) <= 1 for r in rows)
    assert all(r["source"] == "verified-deterministic" for r in rows)
