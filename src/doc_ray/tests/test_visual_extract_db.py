"""DB integration: KIND_VISUAL_EXTRACT writes document_image + Tier-1 features.

Runs against the isolated test database (the ``pg`` fixture refuses live corpus
data). Verifies the migration-089 tables exist, the COPY-jsonb write path works,
crops are stored, provenance is recorded, and a re-run replaces rather than
accumulates.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

from PIL import Image

from doc_ray.adapters import persistence as db
from doc_ray.adapters.pdf_image_source_pymupdf import PyMuPDFImageSource
from doc_ray.adapters.pdf_renderer_pymupdf import PyMuPDFRenderer
from doc_ray.application.visual_extract import extract_document_visuals

_BUCKET = "it-visual"


class _FakeStore:
    """In-memory ObjectStore satisfying the port surface the handler uses."""

    bucket = _BUCKET

    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = dict(blobs)
        self.puts: dict[str, bytes] = {}

    def get(self, key: str) -> bytes:
        return self._blobs[key]

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.puts[key] = data
        return f"s3://{self.bucket}/{key}"

    def ensure_bucket(self) -> None: ...

    def list_objects(self, prefix: str = "") -> list[str]:
        return [k for k in self._blobs if k.startswith(prefix)]

    def delete(self, key: str) -> None:
        self._blobs.pop(key, None)


def _pdf_with_image_blank_and_vector_page() -> bytes:
    """Three pages, one per region source: an embedded raster (p1), a text-free
    page (p2 -> full-page render), and a text-heavy page carrying a vector figure
    (p3 -> rendered-region)."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    buf = BytesIO()
    Image.new("RGB", (40, 40), (10, 180, 60)).save(buf, "PNG")
    page.insert_image(fitz.Rect(20, 20, 120, 120), stream=buf.getvalue())
    doc.new_page(width=200, height=300)  # text-free -> full-page render
    vector_page = doc.new_page(width=200, height=300)  # text-heavy + vector figure
    vector_page.draw_rect(fitz.Rect(30, 30, 170, 200), color=(0, 0, 1), fill=(0.2, 0.4, 0.9))
    for i in range(6):
        vector_page.insert_text((20, 215 + i * 12), f"body prose line {i} beside the figure")
    data: bytes = doc.tobytes()
    doc.close()
    return data


def _count(conn: Any, sql: str, doc_id: str) -> int:
    return int(conn.execute(sql, (doc_id,)).fetchone()["n"])


def test_visual_extract_writes_features_and_replaces(pg: Any) -> None:
    conn, created = pg
    pdf = _pdf_with_image_blank_and_vector_page()
    sha = hashlib.sha256(pdf).hexdigest()
    key = "originals/it-visual.pdf"
    store = _FakeStore({key: pdf})
    doc_id = db.insert_document(
        conn,
        title="vis",
        doc_type="pdf",
        source_uri=f"s3://{_BUCKET}/{key}",
        content_sha256=sha,
        byte_size=len(pdf),
        page_count=3,
        full_text="",
        status="extracted",
        sentences=[],
    )
    created.append(doc_id)
    conn.execute(
        "INSERT INTO document_pages "
        "(document_id, page_number, page_width, page_height, has_text, text_run_count) "
        "VALUES (%s, 1, 200, 300, true, 10), (%s, 2, 200, 300, false, 0), "
        "(%s, 3, 200, 300, true, 20)",
        (doc_id, doc_id, doc_id),
    )

    result = extract_document_visuals(
        conn, store, doc_id, renderer=PyMuPDFRenderer(), image_source=PyMuPDFImageSource()
    )
    assert result["images"] >= 3
    assert result["embedded_rasters"] >= 1
    assert result["full_page_renders"] >= 1
    assert result["rendered_regions"] >= 1

    # The vector figure on the text-heavy page 3 is captured as a rendered-region.
    assert (
        _count(
            conn,
            "SELECT count(*) n FROM document_image "
            "WHERE document_id=%s AND source='rendered-region' AND page_number=3",
            doc_id,
        )
        == 1
    )

    assert (
        _count(conn, "SELECT count(*) n FROM document_image WHERE document_id=%s", doc_id)
        == result["images"]
    )
    assert (
        _count(conn, "SELECT count(*) n FROM image_tier1_features WHERE document_id=%s", doc_id)
        == result["images"]
    )
    assert store.puts  # crops were stored

    row = conn.execute(
        "SELECT dominant_colors, histogram, image_class FROM image_tier1_features "
        "WHERE document_id=%s LIMIT 1",
        (doc_id,),
    ).fetchone()
    assert isinstance(row["dominant_colors"], list) and row["dominant_colors"][0]["hex"].startswith(
        "#"
    )
    assert len(row["histogram"]) == 48
    assert row["image_class"] in ("photo", "diagram", "screenshot", "line-art", "chart")

    assert (
        _count(
            conn,
            "SELECT count(*) n FROM document_provenance "
            "WHERE document_id=%s AND tool='doc-ray/visual-regions'",
            doc_id,
        )
        == 1
    )

    # Re-run replaces, does not accumulate.
    again = extract_document_visuals(
        conn, store, doc_id, renderer=PyMuPDFRenderer(), image_source=PyMuPDFImageSource()
    )
    assert (
        _count(conn, "SELECT count(*) n FROM document_image WHERE document_id=%s", doc_id)
        == again["images"]
    )
