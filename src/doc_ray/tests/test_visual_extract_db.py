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

from PIL import Image, ImageDraw

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


def _busy_png() -> bytes:
    """A non-uniform raster (checkerboard) that clears the content-quality gate —
    a solid-colour or sub-48px crop would (correctly) be dropped as junk."""
    img = Image.new("RGB", (96, 96), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    for i in range(0, 96, 16):
        for j in range(0, 96, 16):
            if (i // 16 + j // 16) % 2 == 0:
                draw.rectangle([i, j, i + 15, j + 15], fill=(20, 120, 200))
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _pdf_with_image_blank_and_vector_page() -> bytes:
    """Three pages, one per region source: an embedded raster (p1), a text-free
    page carrying line-work (p2 -> full-page render), and a text-heavy page with a
    vector figure (p3 -> rendered-region). All crops carry real content so they
    pass the content-quality gate."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.insert_image(fitz.Rect(20, 20, 116, 116), stream=_busy_png())
    grid_page = doc.new_page(width=200, height=300)  # text-free line-work -> full-page render
    for i in range(5):
        grid_page.draw_line((20, 50 + i * 45), (180, 50 + i * 45))
        grid_page.draw_line((20 + i * 40, 40), (20 + i * 40, 240))
    vector_page = doc.new_page(width=200, height=300)  # text-heavy + vector figure
    for i in range(6):
        vector_page.draw_line((30, 34 + i * 28), (170, 34 + i * 28))
        vector_page.draw_line((30 + i * 28, 30), (30 + i * 28, 200))
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


# --- EPUB containers (regression) --------------------------------------------
#
# Tier-1 gated on _is_pdf and returned {"skipped": "not-pdf"} with the job marked
# succeeded, so 283 EPUBs (43% of the corpus, measured 2026-07-29) had an entirely
# empty visual layer and nothing recorded an error. One sampled book held 141
# rasters — 68% of its own bytes — against zero document_image rows.


def _epub_with_one_figure() -> bytes:
    """A minimal but real EPUB: container, OPF spine, one chapter, one raster."""
    import zipfile

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="c1"/></spine></package>',
        )
        zf.writestr("OEBPS/c1.xhtml", '<html><body><img src="fig1.png"/></body></html>')
        zf.writestr("OEBPS/fig1.png", _busy_png())
    return buf.getvalue()


def _make_epub_document(conn: Any, created: list[str], store_key: str) -> tuple[str, Any, bytes]:
    epub = _epub_with_one_figure()
    store = _FakeStore({store_key: epub})
    doc_id = db.insert_document(
        conn,
        title="epub-vis",
        doc_type="epub",
        source_uri=f"s3://{_BUCKET}/{store_key}",
        content_sha256=hashlib.sha256(epub).hexdigest(),
        byte_size=len(epub),
        page_count=None,
        full_text="",
        status="extracted",
        sentences=[],
    )
    created.append(doc_id)
    return doc_id, store, epub


def test_epub_embedded_rasters_are_extracted_and_featurized(pg: Any) -> None:
    from doc_ray.adapters.epub_image_source_zip import ZipEpubImageSource

    conn, created = pg
    doc_id, store, _ = _make_epub_document(conn, created, "originals/it-visual.epub")

    result = extract_document_visuals(
        conn,
        store,
        doc_id,
        renderer=PyMuPDFRenderer(),
        image_source=PyMuPDFImageSource(),
        epub_image_source=ZipEpubImageSource(),
    )

    assert result["images"] == 1
    assert result["embedded_rasters"] == 1
    assert result["container"] == "epub"
    assert store.puts, "the crop must be stored content-addressed"
    # Every image row gets its Tier-1 features, exactly as on the PDF path.
    assert _count(conn, "SELECT count(*) n FROM document_image WHERE document_id=%s", doc_id) == 1
    assert (
        _count(conn, "SELECT count(*) n FROM image_tier1_features WHERE document_id=%s", doc_id)
        == 1
    )


def test_epub_images_carry_spine_position_and_no_invented_geometry(pg: Any) -> None:
    """Reflowable text has no page box, so bbox must be NULL rather than fabricated."""
    from doc_ray.adapters.epub_image_source_zip import ZipEpubImageSource

    conn, created = pg
    doc_id, store, _ = _make_epub_document(conn, created, "originals/it-geom.epub")

    extract_document_visuals(
        conn,
        store,
        doc_id,
        renderer=PyMuPDFRenderer(),
        image_source=PyMuPDFImageSource(),
        epub_image_source=ZipEpubImageSource(),
    )

    row = conn.execute(
        "SELECT page_number, source, xref, bbox_x0, bbox_y0, bbox_x1, bbox_y1, "
        "width_px, height_px, detector FROM document_image WHERE document_id=%s",
        (doc_id,),
    ).fetchone()
    assert row["page_number"] == 1  # spine index of the referencing chapter
    assert row["source"] == "embedded-raster"
    assert row["xref"] is None  # a PDF concept
    assert (row["bbox_x0"], row["bbox_y0"], row["bbox_x1"], row["bbox_y1"]) == (
        None,
        None,
        None,
        None,
    )
    assert row["width_px"] > 0 and row["height_px"] > 0
    assert row["detector"] == "doc-ray/epub-embedded-raster"


def test_epub_re_extraction_replaces_rather_than_accumulates(pg: Any) -> None:
    from doc_ray.adapters.epub_image_source_zip import ZipEpubImageSource

    conn, created = pg
    doc_id, store, _ = _make_epub_document(conn, created, "originals/it-idem.epub")
    kwargs: dict[str, Any] = {
        "renderer": PyMuPDFRenderer(),
        "image_source": PyMuPDFImageSource(),
        "epub_image_source": ZipEpubImageSource(),
    }

    first = extract_document_visuals(conn, store, doc_id, **kwargs)
    second = extract_document_visuals(conn, store, doc_id, **kwargs)

    assert first["images"] == second["images"] == 1
    assert _count(conn, "SELECT count(*) n FROM document_image WHERE document_id=%s", doc_id) == 1


def test_an_unopenable_format_records_which_format_it_skipped(pg: Any) -> None:
    """The silent-success failure mode: a skip must name the format.

    While every non-PDF returned the bare string "not-pdf", an operator reading
    jobs.result could not tell "this document has no images" from "this pipeline
    cannot open this document at all" — which is how 43% of the corpus came to look
    image-free with nobody noticing.
    """
    conn, created = pg
    doc_id = db.insert_document(
        conn,
        title="plain-text",
        doc_type="txt",
        source_uri=f"s3://{_BUCKET}/originals/it-plain.txt",
        content_sha256=hashlib.sha256(b"hello").hexdigest(),
        byte_size=5,
        page_count=None,
        full_text="hello",
        status="extracted",
        sentences=[],
    )
    created.append(doc_id)

    result = extract_document_visuals(
        conn,
        _FakeStore({"originals/it-plain.txt": b"hello"}),
        doc_id,
        renderer=PyMuPDFRenderer(),
        image_source=PyMuPDFImageSource(),
    )

    assert result["skipped"] == "unsupported-format"
    assert result["doc_type"] == "txt"
    assert result["images"] == 0


def test_epub_without_a_wired_source_is_reported_not_silently_empty(pg: Any) -> None:
    # Defence in depth: if a caller forgets to wire the adapter, the result must say
    # so rather than look like a book with no pictures.
    conn, created = pg
    doc_id, store, _ = _make_epub_document(conn, created, "originals/it-nosrc.epub")

    result = extract_document_visuals(
        conn, store, doc_id, renderer=PyMuPDFRenderer(), image_source=PyMuPDFImageSource()
    )

    assert result["skipped"] == "no-epub-image-source"
