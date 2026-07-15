"""Prism Tier-1 orchestrator: detect image regions and store deterministic features.

The ``KIND_VISUAL_EXTRACT`` job body. For one document it: fetches the original
PDF from the object store, detects regions (embedded rasters + full-page renders
of near-text-free pages), stores each crop content-addressed in the object store,
computes deterministic Tier-1 features (``visual_features``), and replaces the
document's ``document_image`` / ``image_tier1_features`` rows in one transaction
with a ``document_provenance`` event.

PALS posture: every value written here is a deterministic function of the crop
bytes plus pinned tool versions — no model output, so no verification queue. The
delete-then-write replacement mirrors every NLP layer's ``(document_id, tool)``
idempotency.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any, cast
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Json

from doc_ray.adapters.pdf_renderer_pymupdf import DEFAULT_RENDER_DPI
from doc_ray.application.integrity import verify_bytes
from doc_ray.application.visual_features import (
    TOOL_NAME,
    TOOL_VERSION,
    Tier1Features,
    extract_tier1_features,
)
from doc_ray.application.visual_regions import PlannedRegion, plan_regions
from doc_ray.domain.progress import report_progress
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.object_store import ObjectStore
from doc_ray.ports.pdf_image_source import PdfImageSource
from doc_ray.ports.pdf_renderer import PdfRenderer

DETECTOR = "doc-ray/visual-regions"
DETECTOR_VERSION = "1.0"

_DOCUMENT_IMAGE_COLUMNS = (
    "id",
    "document_id",
    "page_number",
    "source",
    "xref",
    "bbox_x0",
    "bbox_y0",
    "bbox_x1",
    "bbox_y1",
    "width_px",
    "height_px",
    "object_key",
    "sha256",
    "detector",
    "detector_version",
)
_FEATURE_COLUMNS = (
    "id",
    "document_image_id",
    "document_id",
    "dominant_colors",
    "histogram",
    "mean_saturation",
    "mean_value",
    "contrast",
    "edge_density",
    "image_class",
    "aspect_ratio",
    "width_px",
    "height_px",
    "exif",
    "tool",
    "tool_version",
)


def _object_key_from_source_uri(source_uri: str, bucket: str) -> str | None:
    """Return the object key for a canonical ``s3://bucket/key`` URI."""
    parsed = urlparse(source_uri)
    if parsed.scheme != "s3" or parsed.netloc != bucket:
        return None
    return parsed.path.lstrip("/") or None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _copy_rows(
    cur: psycopg.Cursor[Any], table: str, columns: tuple[str, ...], rows: Sequence[tuple[Any, ...]]
) -> None:
    """Bulk-insert rows via COPY (one server-side op). No-op on empty input.

    Mirrors ``semantic_enrichment._copy_rows``: COPY, not ``executemany``'s
    per-row INSERT storm. Callers must have cleared conflicting rows first (COPY
    has no ``ON CONFLICT``).
    """
    if not rows:
        return
    collist = ", ".join(columns)
    with cur.copy(f"COPY {table} ({collist}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(row)


def _is_pdf(row: dict[str, Any]) -> bool:
    return row.get("doc_type") == "pdf" or row.get("mime_type") == "application/pdf"


def _document_image_row(
    document_id: str, region: PlannedRegion, object_key: str, sha: str
) -> tuple[Any, ...]:
    x0, y0, x1, y1 = region.bbox if region.bbox else (None, None, None, None)
    return (
        new_ulid_str(),
        document_id,
        region.page_number,
        region.source,
        region.xref,
        x0,
        y0,
        x1,
        y1,
        region.width_px,
        region.height_px,
        object_key,
        sha,
        DETECTOR,
        DETECTOR_VERSION,
    )


def _feature_row(
    document_image_id: str, document_id: str, features: Tier1Features
) -> tuple[Any, ...]:
    return (
        new_ulid_str(),
        document_image_id,
        document_id,
        Json(features.palette_json()),
        Json(list(features.histogram)),
        features.mean_saturation,
        features.mean_value,
        features.contrast,
        features.edge_density,
        features.image_class,
        features.aspect_ratio,
        features.width,
        features.height,
        Json(features.exif) if features.exif is not None else None,
        TOOL_NAME,
        TOOL_VERSION,
    )


def _png_size(png: bytes) -> tuple[int, int]:
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(png)) as img:
        return img.width, img.height


def extract_document_visuals(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    document_id: str,
    *,
    renderer: PdfRenderer,
    image_source: PdfImageSource,
) -> dict[str, Any]:
    """Detect regions and write Tier-1 features for one document. Idempotent."""
    cur = conn.execute(
        "SELECT source_uri, mime_type, doc_type, status, content_sha256 "
        "FROM documents WHERE id = %s",
        (document_id,),
    )
    row = cast("dict[str, Any] | None", cur.fetchone())
    if row is None:
        raise RuntimeError(f"document {document_id} not found")
    if row.get("status") != "extracted":
        return {"skipped": "not-extracted", "images": 0}
    if not _is_pdf(row):
        return {"skipped": "not-pdf", "images": 0}

    key = _object_key_from_source_uri(str(row.get("source_uri") or ""), store.bucket)
    if key is None:
        return {"skipped": "no-object-key", "images": 0}
    report_progress(0, 3, "Fetching original PDF")
    pdf = verify_bytes(store.get(key), row.get("content_sha256"), context=f"visual-extract {key}")

    report_progress(1, 3, "Detecting image regions")
    embedded = image_source.embedded_images(pdf)
    runs_cur = conn.execute(
        "SELECT page_number, text_run_count FROM document_pages WHERE document_id = %s",
        (document_id,),
    )
    page_text_runs = {int(r["page_number"]): int(r["text_run_count"]) for r in runs_cur.fetchall()}

    def _render_full_page(page_number: int) -> tuple[bytes, int, int]:
        png = renderer.render_page_png(pdf, page_number, dpi=DEFAULT_RENDER_DPI)
        width, height = _png_size(png)
        return png, width, height

    regions = plan_regions(embedded, page_text_runs, _render_full_page)

    version = str(row.get("content_sha256") or document_id)
    image_rows: list[tuple[Any, ...]] = []
    feature_rows: list[tuple[Any, ...]] = []
    report_progress(2, 3, f"Featurizing {len(regions)} regions")
    for region in regions:
        sha = _sha256(region.png_bytes)
        object_key = (
            f"document-images/{version}/p{region.page_number}-{region.source}-{sha[:16]}.png"
        )
        store.put(object_key, region.png_bytes, "image/png")
        image_row = _document_image_row(document_id, region, object_key, sha)
        image_rows.append(image_row)
        features = extract_tier1_features(region.png_bytes)
        feature_rows.append(_feature_row(cast(str, image_row[0]), document_id, features))

    with conn.transaction():
        # Replace prior rows: deleting document_image cascades to image_tier1_features.
        conn.execute("DELETE FROM document_image WHERE document_id = %s", (document_id,))
        with conn.cursor() as write_cur:
            _copy_rows(write_cur, "document_image", _DOCUMENT_IMAGE_COLUMNS, image_rows)
            _copy_rows(write_cur, "image_tier1_features", _FEATURE_COLUMNS, feature_rows)
        conn.execute(
            "INSERT INTO document_provenance (id, document_id, kind, tool, tool_version, params) "
            "VALUES (%s, %s, 'indexed', %s, %s, %s)",
            (
                new_ulid_str(),
                document_id,
                DETECTOR,
                DETECTOR_VERSION,
                Json({"images": len(regions)}),
            ),
        )

    embedded_count = sum(1 for r in regions if r.source == "embedded-raster")
    return {
        "images": len(regions),
        "embedded_rasters": embedded_count,
        "full_page_renders": len(regions) - embedded_count,
    }
