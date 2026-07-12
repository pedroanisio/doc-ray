from __future__ import annotations

from doc_ray.adapters import persistence


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
