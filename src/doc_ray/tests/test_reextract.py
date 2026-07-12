from __future__ import annotations

import contextlib
import hashlib
from types import SimpleNamespace

import pytest

from doc_ray.application import reextract
from doc_ray.domain import progress as domain_progress

DOC_ID = "11111111-1111-1111-1111-111111111111"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))
        if "FROM documents d" in sql:
            return _Rows(self.rows)
        return _Rows([self.row] if self.row else [])


class _Store:
    bucket = "docray-test"

    def __init__(self, blobs):
        self.blobs = blobs
        self.keys = []

    def get(self, key):
        self.keys.append(key)
        return self.blobs[key]


def test_backfill_document_geometry_from_store_fetches_original_blob(monkeypatch) -> None:
    data = b"%PDF"
    conn = _Conn(
        {
            "source_uri": "s3://docray-test/aa/report.pdf",
            "content_sha256": hashlib.sha256(data).hexdigest(),
            "original_filename": "report.pdf",
            "doc_type": "pdf",
        }
    )
    store = _Store({"aa/report.pdf": data})
    calls = []

    def fake_backfill(conn_arg, doc_id, data, filename, ext):
        calls.append((conn_arg, doc_id, data, filename, ext))
        return {"layout_runs": 3, "spans_positioned": 9}

    monkeypatch.setattr(reextract, "backfill_document_geometry", fake_backfill)

    result = reextract.backfill_document_geometry_from_store(conn, store, DOC_ID)

    assert result == {"layout_runs": 3, "spans_positioned": 9}
    assert store.keys == ["aa/report.pdf"]
    assert calls == [(conn, DOC_ID, b"%PDF", "report", ".pdf")]


def test_enqueue_reextract_jobs_selects_pdfs_missing_position_data(monkeypatch) -> None:
    conn = _Conn(
        rows=[
            {"id": "doc-a"},
            {"id": "doc-b"},
        ]
    )
    enqueued = []
    monkeypatch.setattr(
        "doc_ray.adapters.queue_postgres.enqueue",
        lambda conn_arg, kind, payload, **kw: (
            enqueued.append((conn_arg, kind, payload, kw)) or f"job-{len(enqueued)}"
        ),
    )

    jobs = reextract.enqueue_reextract_jobs(conn, only_missing_geometry=True)

    assert jobs == ["job-1", "job-2"]
    assert [call[1] for call in enqueued] == ["reextract", "reextract"]
    assert [call[3]["document_id"] for call in enqueued] == ["doc-a", "doc-b"]
    sql = conn.executed[0][0]
    assert "d.doc_type = 'pdf'" in sql
    assert "sp.page_number IS NOT NULL" in sql


def test_object_key_from_source_uri_rejects_other_buckets() -> None:
    assert reextract.object_key_from_source_uri(
        "s3://docray-test/aa/report.pdf", "docray-test"
    ) == ("aa/report.pdf")
    with pytest.raises(ValueError, match="does not belong to bucket"):
        reextract.object_key_from_source_uri("s3://other/aa/report.pdf", "docray-test")


def test_backfill_document_geometry_refuses_text_drift(monkeypatch) -> None:
    conn = _Conn({"full_text": "Stored text."})
    monkeypatch.setattr(
        reextract.extract_mod,
        "extract",
        lambda *_args, **_kwargs: SimpleNamespace(
            full_text="Different text.",
            page_runs=[],
            page_count=1,
            extractor="fake",
        ),
    )

    with pytest.raises(RuntimeError, match="text drift"):
        reextract.backfill_document_geometry(conn, DOC_ID, b"pdf", "report", ".pdf")


def test_backfill_document_geometry_reports_page_progress(monkeypatch) -> None:
    class ProgressConn:
        def execute(self, sql, params=()):
            if "SELECT full_text" in sql:
                return _Rows([{"full_text": "Stored text."}])
            if "SELECT char_start, char_end" in sql:
                return _Rows([{"char_start": 0, "char_end": 6}])
            if "count(*) AS n" in sql:
                return _Rows([{"n": 1}])
            return _Rows([])

        def transaction(self):
            return contextlib.nullcontext()

    monkeypatch.setattr(
        reextract.extract_mod,
        "extract",
        lambda *_args, **_kwargs: SimpleNamespace(
            full_text="Stored text.",
            page_runs=[
                {"page": 0, "text": "Stored", "bbox": (0, 0, 1, 1)},
                {"page": 1, "text": "text.", "bbox": (0, 0, 1, 1)},
            ],
            page_count=2,
            extractor="fake",
        ),
    )
    monkeypatch.setattr(
        reextract.db,
        "layout_runs_from_page_runs",
        lambda *_args: [{"page_number": 1}, {"page_number": 2}],
    )
    monkeypatch.setattr(reextract.db, "replace_document_layout_runs", lambda *_args: None)
    monkeypatch.setattr(reextract.db, "ensure_spans", lambda *_args: {})

    events: list[tuple[int | None, int | None, str | None]] = []
    token = domain_progress.set_progress_reporter(
        lambda current, total, stage: events.append((current, total, stage))
    )
    try:
        result = reextract.backfill_document_geometry(
            ProgressConn(),
            DOC_ID,
            b"pdf",
            "report",
            ".pdf",  # type: ignore[arg-type]
        )
    finally:
        domain_progress.reset_progress_reporter(token)

    assert result["layout_runs"] == 2
    assert events == [
        (0, None, "Re-extracting document geometry"),
        (1, 4, "Projecting geometry for page 1 of 2"),
        (2, 4, "Projecting geometry for page 2 of 2"),
        (3, 4, "Persisting geometry"),
    ]
