"""Unit coverage for corpus-wide semantic embedding job selection."""

from __future__ import annotations

import asyncio
from typing import Any

from doc_ray.adapters.text_encoder_sentence_transformers import DEFAULT_MODEL_TAG
from doc_ray.application.semantic_embed import enqueue_semantic_embed_jobs


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.sql = ""
        self.params: dict[str, Any] = {}

    async def execute(self, sql: str, params: dict[str, Any]) -> _Cursor:
        self.sql = sql
        self.params = params
        return _Cursor(self._rows)


def test_semantic_backfill_selects_only_missing_active_model_documents() -> None:
    conn = _Connection([{"id": "doc-a"}, {"id": "doc-b"}])
    enqueued: list[str] = []

    async def enqueue_document(document_id: str) -> str:
        enqueued.append(document_id)
        return f"job-{document_id}"

    jobs = asyncio.run(
        enqueue_semantic_embed_jobs(conn, only_missing=True, enqueue_document=enqueue_document)
    )

    assert jobs == ["job-doc-a", "job-doc-b"]
    assert enqueued == ["doc-a", "doc-b"]
    assert "NOT EXISTS" in conn.sql
    assert "e.model = %(model)s" in conn.sql
    assert conn.params == {"model": DEFAULT_MODEL_TAG}


def test_semantic_backfill_all_requeues_every_sentence_bearing_document() -> None:
    conn = _Connection([{"id": "doc-a"}])

    async def enqueue_document(document_id: str) -> str:
        return f"job-{document_id}"

    jobs = asyncio.run(
        enqueue_semantic_embed_jobs(conn, only_missing=False, enqueue_document=enqueue_document)
    )

    assert jobs == ["job-doc-a"]
    assert "NOT EXISTS" not in conn.sql
    assert "EXISTS (SELECT 1 FROM sentences" in conn.sql
