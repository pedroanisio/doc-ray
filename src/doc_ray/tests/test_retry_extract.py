"""The repair path for a document whose ingest read no text.

Regression cover for the state a scanned PDF used to be stuck in forever:
``status='failed'``, bytes in the store, and no way back — ingest dedupes by
content hash, so re-uploading the same file reports the same failure, and the
geometry re-extract path only ever selected documents that already had text.
"""

from __future__ import annotations

import contextlib
import hashlib
from types import SimpleNamespace
from typing import Any

import pytest

from doc_ray.application import retry_extract
from doc_ray.application.pipeline import NO_EXTRACTABLE_TEXT

DOC_ID = "11111111-1111-1111-1111-111111111111"
PDF = b"%PDF-scan"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def executemany(self, sql: str, rows: list[Any]) -> None:
        self.conn.executed.append((" ".join(sql.split()), list(rows)))


class _Conn:
    """Records SQL; answers the one SELECT the repair issues."""

    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row
        self.executed: list[tuple[str, Any]] = []
        self.transactions = 0

    def execute(self, sql: str, params: Any = ()) -> _Rows:
        self.executed.append((" ".join(sql.split()), params))
        if "FROM documents WHERE id" in " ".join(sql.split()):
            return _Rows([self.row] if self.row else [])
        return _Rows([])

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def transaction(self) -> Any:
        self.transactions += 1
        return contextlib.nullcontext()

    def statements(self) -> str:
        return "\n".join(sql for sql, _params in self.executed)


class _Store:
    bucket = "docray-test"

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs
        self.keys: list[str] = []

    def get(self, key: str) -> bytes:
        self.keys.append(key)
        return self.blobs[key]


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": DOC_ID,
        "title": "US2022099404A1",
        "status": "failed",
        "source_uri": "s3://docray-test/aa/scan.pdf",
        "content_sha256": hashlib.sha256(PDF).hexdigest(),
        "byte_size": len(PDF),
        "original_filename": "US2022099404A1.pdf",
        "doc_type": "pdf",
    }
    row.update(overrides)
    return row


def _extracted(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        full_text=text,
        page_count=3,
        page_runs=[],
        page_metrics=None,
        extractor="pymupdf+ocr",
        extractor_version="1.24.0",
        content_type="application/pdf",
    )


def _patch_extractor(monkeypatch, result: Any) -> None:
    def fake_extract(*_args: Any, **_kwargs: Any) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(retry_extract.extract_mod, "extract", fake_extract)
    monkeypatch.setattr(retry_extract.db, "replace_document_pages", lambda *_a, **_k: None)
    monkeypatch.setattr(retry_extract.db, "replace_document_layout_runs", lambda *_a, **_k: None)
    monkeypatch.setattr(
        retry_extract.db, "ensure_spans", lambda _c, _d, offsets: dict.fromkeys(offsets, "span-1")
    )
    monkeypatch.setattr(retry_extract.db, "append_provenance_events", lambda *_a, **_k: None)


def test_a_failed_scan_is_promoted_once_the_extractor_can_read_it(monkeypatch) -> None:
    conn = _Conn(_row())
    store = _Store({"aa/scan.pdf": PDF})
    _patch_extractor(monkeypatch, _extracted("Recovered page one. Recovered page two."))

    result = retry_extract.retry_failed_extraction(conn, store, DOC_ID)

    assert result["extractor"] == "pymupdf+ocr"
    assert result["chars"] > 0 and result["sentences"] > 0
    statements = conn.statements()
    assert "status = 'extracted'" in statements
    assert "error = NULL" in statements
    assert "UPDATE document_content SET full_text" in statements
    assert store.keys == ["aa/scan.pdf"]


def test_a_retry_that_still_reads_nothing_leaves_the_document_failed(monkeypatch) -> None:
    """Half-promoting an empty document is worse than the failure it replaces."""
    conn = _Conn(_row())
    _patch_extractor(monkeypatch, _extracted("   "))

    with pytest.raises(RuntimeError, match="no extractable text"):
        retry_extract.retry_failed_extraction(conn, _Store({"aa/scan.pdf": PDF}), DOC_ID)

    statements = conn.statements()
    assert "status = 'extracted'" not in statements
    assert ("UPDATE documents SET error = %s WHERE id = %s", (NO_EXTRACTABLE_TEXT, DOC_ID)) in [
        (sql, params) for sql, params in conn.executed
    ]


def test_an_extractor_failure_replaces_the_stale_error_with_this_attempts_reason(
    monkeypatch,
) -> None:
    conn = _Conn(_row())
    _patch_extractor(monkeypatch, RuntimeError("OCR pass failed: tesseract not found"))

    with pytest.raises(RuntimeError, match="tesseract not found"):
        retry_extract.retry_failed_extraction(conn, _Store({"aa/scan.pdf": PDF}), DOC_ID)

    recorded = [params for sql, params in conn.executed if "SET error = %s" in sql]
    assert recorded and "tesseract not found" in recorded[0][0]


def test_a_healthy_document_is_refused(monkeypatch) -> None:
    """Repairing a document that has text would delete work; that is reextract's job."""
    conn = _Conn(_row(status="extracted"))

    with pytest.raises(retry_extract.NotRepairable, match="not 'failed'"):
        retry_extract.retry_failed_extraction(conn, _Store({}), DOC_ID)


def test_a_failed_document_with_no_stored_blob_is_refused() -> None:
    conn = _Conn(_row(source_uri=None))

    with pytest.raises(retry_extract.NotRepairable, match="no stored original"):
        retry_extract.retry_failed_extraction(conn, _Store({}), DOC_ID)


def test_a_missing_document_is_refused() -> None:
    with pytest.raises(retry_extract.NotRepairable, match="not found"):
        retry_extract.retry_failed_extraction(_Conn(None), _Store({}), DOC_ID)


def test_corrupt_stored_bytes_are_refused(monkeypatch) -> None:
    """Hash-verified like every other read of the original (BL-080 integrity)."""
    conn = _Conn(_row(content_sha256=hashlib.sha256(b"different").hexdigest()))

    with pytest.raises(Exception, match="sha256"):
        retry_extract.retry_failed_extraction(conn, _Store({"aa/scan.pdf": PDF}), DOC_ID)


class TestHandler:
    """The queue-facing half: a repaired document owes the whole pipeline."""

    def _job(self, payload: dict[str, Any] | None = None) -> Any:
        return SimpleNamespace(document_id=DOC_ID, payload=payload or {}, kind="retry_extract")

    def _patch(self, monkeypatch, result: Any) -> list[tuple[str, Any]]:
        from doc_ray.application import jobs

        chained: list[tuple[str, Any]] = []
        monkeypatch.setattr(retry_extract, "refresh_document_stats", lambda *_a, **_k: None)
        monkeypatch.setattr(
            retry_extract,
            "retry_failed_extraction",
            lambda *_a, **_k: (
                (_ for _ in ()).throw(result) if isinstance(result, Exception) else result
            ),
        )
        monkeypatch.setattr(
            jobs,
            "enqueue_chain_head",
            lambda _conn, doc_id, chain, meta=None: chained.append((doc_id, tuple(chain))),
        )
        return chained

    def test_a_repaired_document_enters_the_default_pipeline(self, monkeypatch) -> None:
        """It has no annotations, entities or embeddings — its ingest produced none."""
        from doc_ray.application.retry_extract import handle_retry_extract
        from doc_ray.domain.jobs import DEFAULT_PIPELINE_CHAIN

        chained = self._patch(monkeypatch, {"extractor": "pymupdf+ocr", "chars": 42})

        result = handle_retry_extract(_Conn(_row()), _Store({}), self._job())

        assert result["extractor"] == "pymupdf+ocr"
        assert chained == [(DOC_ID, tuple(DEFAULT_PIPELINE_CHAIN))]

    def test_a_failed_repair_starts_no_pipeline(self, monkeypatch) -> None:
        from doc_ray.application.retry_extract import handle_retry_extract

        chained = self._patch(monkeypatch, RuntimeError(NO_EXTRACTABLE_TEXT))

        with pytest.raises(RuntimeError, match="no extractable text"):
            handle_retry_extract(_Conn(_row()), _Store({}), self._job())

        assert chained == []

    def test_an_explicit_payload_chain_is_not_duplicated(self, monkeypatch) -> None:
        from doc_ray.application.retry_extract import handle_retry_extract

        chained = self._patch(monkeypatch, {"extractor": "pymupdf", "chars": 1})

        handle_retry_extract(_Conn(_row()), _Store({}), self._job({"chain": ["annotate"]}))

        assert chained == []

    def test_the_handler_is_registered_for_the_job_kind(self) -> None:
        from doc_ray.application.job_handlers import HANDLERS
        from doc_ray.application.retry_extract import handle_retry_extract
        from doc_ray.domain.jobs import KIND_RETRY_EXTRACT

        assert HANDLERS[KIND_RETRY_EXTRACT] is handle_retry_extract
