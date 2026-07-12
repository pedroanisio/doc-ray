from __future__ import annotations

from doc_ray.adapters import persistence


class _RecordingCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def executemany(self, sql: str, rows: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((sql, rows))


class _RecordingConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.cursor_obj = _RecordingCursor()

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executed.append((sql, params))

    def cursor(self) -> _RecordingCursor:
        return self.cursor_obj


def test_layout_runs_from_page_runs_maps_pdf_lines_to_text_offsets() -> None:
    text = "First page title\nSecond page paragraph\n"
    runs = persistence.layout_runs_from_page_runs(
        text,
        [
            {"page": 0, "bbox": (10.0, 20.0, 110.0, 30.0), "text": "First page title"},
            {
                "page": 1,
                "page_width": 200.0,
                "page_height": 300.0,
                "bbox": (12.0, 40.0, 180.0, 52.0),
                "text": "Second page paragraph",
            },
        ],
    )

    assert runs == [
        {
            "ordinal": 0,
            "page_number": 1,
            "page_width": None,
            "page_height": None,
            "char_start": 0,
            "char_end": 16,
            "bbox_x0": 10.0,
            "bbox_y0": 20.0,
            "bbox_x1": 110.0,
            "bbox_y1": 30.0,
            "text": "First page title",
        },
        {
            "ordinal": 1,
            "page_number": 2,
            "page_width": 200.0,
            "page_height": 300.0,
            "char_start": 17,
            "char_end": 38,
            "bbox_x0": 12.0,
            "bbox_y0": 40.0,
            "bbox_x1": 180.0,
            "bbox_y1": 52.0,
            "text": "Second page paragraph",
        },
    ]


def test_page_rows_from_page_metrics_maps_zero_based_pages_to_db_rows() -> None:
    rows = persistence.page_rows_from_page_metrics(
        [
            {
                "page": 0,
                "page_width": 320.0,
                "page_height": 480.0,
                "has_text": False,
                "text_run_count": 0,
            },
            {
                "page": 1,
                "page_width": 640.0,
                "page_height": 360.0,
                "has_text": True,
                "text_run_count": 3,
            },
        ]
    )

    assert rows == [
        {
            "page_number": 1,
            "page_width": 320.0,
            "page_height": 480.0,
            "has_text": False,
            "text_run_count": 0,
        },
        {
            "page_number": 2,
            "page_width": 640.0,
            "page_height": 360.0,
            "has_text": True,
            "text_run_count": 3,
        },
    ]


def test_replace_document_pages_deletes_then_upserts_page_metrics() -> None:
    conn = _RecordingConn()

    persistence.replace_document_pages(
        conn,  # type: ignore[arg-type]
        "doc-1",
        [
            {
                "page": 0,
                "page_width": 320.0,
                "page_height": 480.0,
                "has_text": False,
                "text_run_count": 0,
            },
            {
                "page": 1,
                "page_width": 640.0,
                "page_height": 360.0,
                "has_text": True,
                "text_run_count": 2,
            },
        ],
    )

    assert conn.executed == [("DELETE FROM document_pages WHERE document_id=%s", ("doc-1",))]
    assert len(conn.cursor_obj.executemany_calls) == 1
    sql, rows = conn.cursor_obj.executemany_calls[0]
    assert "INSERT INTO document_pages" in sql
    assert rows == [
        ("doc-1", 1, 320.0, 480.0, False, 0),
        ("doc-1", 2, 640.0, 360.0, True, 2),
    ]


def test_replace_document_pages_with_no_metrics_only_deletes_stale_rows() -> None:
    conn = _RecordingConn()

    persistence.replace_document_pages(conn, "doc-1", None)  # type: ignore[arg-type]

    assert conn.executed == [("DELETE FROM document_pages WHERE document_id=%s", ("doc-1",))]
    assert conn.cursor_obj.executemany_calls == []


def test_span_geometry_from_layout_rows_uses_largest_overlap_page() -> None:
    layout = [
        {
            "page_number": 1,
            "char_start": 0,
            "char_end": 14,
            "bbox_x0": 10.0,
            "bbox_y0": 20.0,
            "bbox_x1": 90.0,
            "bbox_y1": 32.0,
        },
        {
            "page_number": 1,
            "char_start": 15,
            "char_end": 28,
            "bbox_x0": 12.0,
            "bbox_y0": 38.0,
            "bbox_x1": 120.0,
            "bbox_y1": 50.0,
        },
        {
            "page_number": 2,
            "char_start": 29,
            "char_end": 34,
            "bbox_x0": 14.0,
            "bbox_y0": 10.0,
            "bbox_x1": 80.0,
            "bbox_y1": 22.0,
        },
    ]

    geometry = persistence.span_geometry_from_layout_rows([(3, 20), (25, 33)], layout)

    assert geometry[(3, 20)] == {
        "page_number": 1,
        "bbox_x0": 10.0,
        "bbox_y0": 20.0,
        "bbox_x1": 120.0,
        "bbox_y1": 50.0,
    }
    assert geometry[(25, 33)] == {
        "page_number": 2,
        "bbox_x0": 14.0,
        "bbox_y0": 10.0,
        "bbox_x1": 80.0,
        "bbox_y1": 22.0,
    }
