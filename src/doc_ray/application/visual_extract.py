"""Prism Tier-1 orchestrator: detect image regions and store deterministic features.

The ``KIND_VISUAL_EXTRACT`` job body. For one document it: fetches the original
PDF from the object store, detects regions (embedded rasters + rasterized
vector-figure clusters + full-page renders of near-text-free pages), stores each
crop content-addressed in the object store,
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

from doc_ray.application.integrity import verify_bytes
from doc_ray.application.visual_features import (
    TOOL_NAME,
    TOOL_VERSION,
    Tier1Features,
    extract_tier1_features,
    is_meaningful_region,
)
from doc_ray.application.visual_regions import PlannedRegion, plan_regions
from doc_ray.domain.progress import report_progress
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.epub_image_source import EpubImage, EpubImageSource
from doc_ray.ports.object_store import ObjectStore
from doc_ray.ports.pdf_image_source import PdfImageSource
from doc_ray.ports.pdf_renderer import DEFAULT_RENDER_DPI, PdfRenderer

DETECTOR = "doc-ray/visual-regions"
DETECTOR_VERSION = "1.0"
#: EPUB rasters are detected by a different, geometry-free path, so they carry
#: their own detector identity rather than claiming the PDF region planner's.
EPUB_DETECTOR = "doc-ray/epub-embedded-raster"
EPUB_DETECTOR_VERSION = "1.0"

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


def _is_epub(row: dict[str, Any]) -> bool:
    return row.get("doc_type") == "epub" or row.get("mime_type") == "application/epub+zip"


def _commit_visuals(
    conn: psycopg.Connection[Any],
    document_id: str,
    image_rows: list[tuple[Any, ...]],
    feature_rows: list[tuple[Any, ...]],
    summary: dict[str, Any],
    *,
    detector: str,
    detector_version: str,
    note: str,
) -> dict[str, Any]:
    """Replace this document's visual rows and stamp provenance, in one transaction.

    Shared by the PDF and EPUB paths so the two cannot drift on the replacement
    semantics that make re-extraction idempotent.
    """
    provenance_id = new_ulid_str()
    with conn.transaction():
        # Replace prior rows: deleting document_image cascades to image_tier1_features.
        conn.execute("DELETE FROM document_image WHERE document_id = %s", (document_id,))
        with conn.cursor() as write_cur:
            _copy_rows(write_cur, "document_image", _DOCUMENT_IMAGE_COLUMNS, image_rows)
            _copy_rows(write_cur, "image_tier1_features", _FEATURE_COLUMNS, feature_rows)
        # Provenance params live in provenance_params((event_id, key, value)) — the
        # document_provenance.params column was dropped in migration 004.
        conn.execute(
            "INSERT INTO document_provenance "
            "(id, document_id, kind, tool, tool_version, actor, note) "
            "VALUES (%s, %s, 'indexed', %s, %s, %s, %s)",
            (
                provenance_id,
                document_id,
                detector,
                detector_version,
                "doc_ray.application.visual_extract",
                note,
            ),
        )
        with conn.cursor() as pcur:
            pcur.executemany(
                "INSERT INTO provenance_params (event_id, key, value) VALUES (%s, %s, %s)",
                [(provenance_id, key, str(value)) for key, value in summary.items()],
            )
    return summary


def _epub_document_image_row(
    document_id: str, image: EpubImage, object_key: str, sha: str
) -> tuple[Any, ...]:
    """One ``document_image`` row for an EPUB raster: spine position, no geometry."""
    return (
        new_ulid_str(),
        document_id,
        image.spine_index,
        "embedded-raster",
        None,  # xref: a PDF concept
        None,  # bbox_x0
        None,  # bbox_y0
        None,  # bbox_x1
        None,  # bbox_y1  — reflowable text has no placement box
        image.width_px,
        image.height_px,
        object_key,
        sha,
        EPUB_DETECTOR,
        EPUB_DETECTOR_VERSION,
    )


def _extract_epub_visuals(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    document_id: str,
    *,
    epub_bytes: bytes,
    image_source: EpubImageSource,
    version: str,
) -> dict[str, Any]:
    """Store every embedded raster of an EPUB with its Tier-1 features."""
    report_progress(1, 3, "Detecting embedded rasters")
    images = image_source.embedded_images(epub_bytes)

    image_rows: list[tuple[Any, ...]] = []
    feature_rows: list[tuple[Any, ...]] = []
    seen: set[tuple[int, str]] = set()
    skipped_lowvalue = 0
    report_progress(2, 3, f"Featurizing {len(images)} rasters")
    for image in images:
        features = extract_tier1_features(image.png_bytes)
        # Same content gate as the PDF path: spacers, rules, and blank crops are
        # not figures, and they otherwise flood the corpus.
        if not is_meaningful_region(features):
            skipped_lowvalue += 1
            continue
        sha = _sha256(image.png_bytes)
        # document_image is UNIQUE(document_id, page_number, source, sha256) and
        # _copy_rows uses COPY (no ON CONFLICT), so a duplicate would abort the job.
        dedup_key = (image.spine_index, sha)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        object_key = f"document-images/{version}/s{image.spine_index}-embedded-{sha[:16]}.png"
        store.put(object_key, image.png_bytes, "image/png")
        image_row = _epub_document_image_row(document_id, image, object_key, sha)
        image_rows.append(image_row)
        feature_rows.append(_feature_row(cast(str, image_row[0]), document_id, features))

    summary = {
        "images": len(image_rows),
        "embedded_rasters": len(image_rows),
        "rendered_regions": 0,
        "full_page_renders": 0,
        "skipped_lowvalue": skipped_lowvalue,
        "container": "epub",
    }
    return _commit_visuals(
        conn,
        document_id,
        image_rows,
        feature_rows,
        summary,
        detector=EPUB_DETECTOR,
        detector_version=EPUB_DETECTOR_VERSION,
        note="EPUB embedded rasters committed with replacement semantics; "
        "positioned by spine index, no page geometry.",
    )


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
    epub_image_source: EpubImageSource | None = None,
) -> dict[str, Any]:
    """Detect regions and write Tier-1 features for one document. Idempotent.

    Two container families are handled, because a format this cannot open is a
    document whose whole visual layer is absent from the corpus:

    * **PDF** — full region planning: embedded rasters, rasterized vector-figure
      clusters, and full-page renders of near-text-free pages, all with page
      geometry.
    * **EPUB** — embedded rasters only, positioned by spine index with no bbox
      (see :mod:`doc_ray.adapters.epub_image_source_zip` for why inventing page
      geometry for reflowable text would be storing a fabricated fact).

    Anything else still records a skip, but ``skipped`` now names the format so an
    operator reading ``jobs.result`` can tell "this document has no images" apart
    from "this pipeline cannot see this document's images".
    """
    cur = conn.execute(
        "SELECT source_uri, mime_type, doc_type, status, content_sha256 "
        "FROM documents WHERE id = %s",
        (document_id,),
    )
    row = cast("dict[str, Any] | None", cur.fetchone())
    if row is None:
        raise RuntimeError(f"document {document_id} not found")
    refusal = _preflight(row, epub_image_source=epub_image_source)
    if refusal is not None:
        return refusal

    key = _object_key_from_source_uri(str(row.get("source_uri") or ""), store.bucket)
    if key is None:
        return {"skipped": "no-object-key", "images": 0}
    report_progress(0, 3, "Fetching original document")
    data = verify_bytes(store.get(key), row.get("content_sha256"), context=f"visual-extract {key}")
    version = str(row.get("content_sha256") or document_id)

    if _is_epub(row):
        assert epub_image_source is not None  # _preflight rejected the None case
        return _extract_epub_visuals(
            conn,
            store,
            document_id,
            epub_bytes=data,
            image_source=epub_image_source,
            version=version,
        )
    return _extract_pdf_visuals(
        conn,
        store,
        document_id,
        pdf=data,
        renderer=renderer,
        image_source=image_source,
        version=version,
    )


def _preflight(
    row: dict[str, Any], *, epub_image_source: EpubImageSource | None
) -> dict[str, Any] | None:
    """Return a skip result when this document cannot be processed, else ``None``.

    Each refusal names its own reason: an operator reading ``jobs.result`` has to be
    able to distinguish a document with no images from one whose format the
    pipeline never opened, which is the ambiguity that hid an empty visual layer
    across 43% of the corpus.
    """
    # 'failed' documents are the scanned/image-only PDFs (near-zero extractable
    # text) — the highest-value visual targets, so Tier-1 must include them, not
    # only 'extracted' docs (proposal §2 "image-only documents").
    if row.get("status") not in ("extracted", "failed"):
        return {"skipped": "not-ready", "images": 0}
    if not _is_pdf(row) and not _is_epub(row):
        return {"skipped": "unsupported-format", "doc_type": row.get("doc_type"), "images": 0}
    if _is_epub(row) and epub_image_source is None:
        return {"skipped": "no-epub-image-source", "images": 0}
    return None


def _extract_pdf_visuals(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    document_id: str,
    *,
    pdf: bytes,
    renderer: PdfRenderer,
    image_source: PdfImageSource,
    version: str,
) -> dict[str, Any]:
    """Plan and store every region of a PDF: rasters, vector figures, page renders."""
    report_progress(1, 3, "Detecting image regions")
    embedded = image_source.embedded_images(pdf)
    vector_figures = image_source.vector_figures(pdf)
    runs_cur = conn.execute(
        "SELECT page_number, text_run_count FROM document_pages WHERE document_id = %s",
        (document_id,),
    )
    page_text_runs = {int(r["page_number"]): int(r["text_run_count"]) for r in runs_cur.fetchall()}

    def _render_full_page(page_number: int) -> tuple[bytes, int, int]:
        png = renderer.render_page_png(pdf, page_number, dpi=DEFAULT_RENDER_DPI)
        width, height = _png_size(png)
        return png, width, height

    def _render_region(
        page_number: int, bbox: tuple[float, float, float, float]
    ) -> tuple[bytes, int, int]:
        png = renderer.render_region_png(pdf, page_number, bbox, dpi=DEFAULT_RENDER_DPI)
        width, height = _png_size(png)
        return png, width, height

    regions = plan_regions(
        embedded,
        page_text_runs,
        _render_full_page,
        vector_figures,
        _render_region,
    )

    image_rows: list[tuple[Any, ...]] = []
    feature_rows: list[tuple[Any, ...]] = []
    # Dedup byte-identical crops per (page, source): one embedded image placed
    # multiple times on a page (repeated logo/icon/watermark) yields identical
    # regions, which would violate document_image's UNIQUE(document_id,
    # page_number, source, sha256) under COPY (no ON CONFLICT) and abort the job.
    # Identical crops have identical deterministic features, so one row suffices.
    seen: set[tuple[int, str, str]] = set()
    skipped_lowvalue = 0
    report_progress(2, 3, f"Featurizing {len(regions)} regions")
    for region in regions:
        features = extract_tier1_features(region.png_bytes)
        # Content-quality gate: never store page-background slivers, hairline
        # rules, tiny logos, or blank crops — they are not figures and otherwise
        # flood the corpus (deterministic over the crop's own Tier-1 features).
        if not is_meaningful_region(features):
            skipped_lowvalue += 1
            continue
        sha = _sha256(region.png_bytes)
        dedup_key = (region.page_number, region.source, sha)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        object_key = (
            f"document-images/{version}/p{region.page_number}-{region.source}-{sha[:16]}.png"
        )
        store.put(object_key, region.png_bytes, "image/png")
        image_row = _document_image_row(document_id, region, object_key, sha)
        image_rows.append(image_row)
        feature_rows.append(_feature_row(cast(str, image_row[0]), document_id, features))

    # Count what is actually written (post-dedup): source is column index 3.
    def _count(source: str) -> int:
        return sum(1 for r in image_rows if r[3] == source)

    summary = {
        "images": len(image_rows),
        "embedded_rasters": _count("embedded-raster"),
        "rendered_regions": _count("rendered-region"),
        "full_page_renders": _count("full-page"),
        "skipped_lowvalue": skipped_lowvalue,
    }
    return _commit_visuals(
        conn,
        document_id,
        image_rows,
        feature_rows,
        summary,
        detector=DETECTOR,
        detector_version=DETECTOR_VERSION,
        note="Deterministic Tier-1 visual features committed with replacement semantics.",
    )
