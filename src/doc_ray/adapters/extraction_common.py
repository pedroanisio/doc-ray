"""Shared extraction primitives: the result dataclass and cross-format guards.

Split out of :mod:`doc_ray.adapters.extraction` (D-015). Every format-family
module builds on these; the dispatcher re-exports :class:`Extracted` for
callers.
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Extracted:
    """Normalized result of extracting text and metadata from one document."""

    doc_type: str
    title: str
    author: str | None
    page_count: int | None
    full_text: str
    content_type: str
    extractor: str  # tool that produced the text
    extractor_version: str  # its version — recorded in provenance
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Per-line source geometry (BL-076), PDF-pymupdf only: each entry is
    #: {"page": int, "bbox": (x0, y0, x1, y1), "text": str}. A sibling view over
    #: the same geometry the layout classifier already computes — it does not
    #: change full_text/to_markdown's output, only surfaces what was already
    #: being discarded. None for every other extractor.
    page_runs: list[dict[str, Any]] | None = None
    #: Per-page source geometry for paginated formats, PDF-pymupdf today. Each
    #: entry is {"page": int, "page_width": float, "page_height": float,
    #: "has_text": bool, "text_run_count": int}. Unlike page_runs, this includes
    #: pages with no extracted text.
    page_metrics: list[dict[str, Any]] | None = None


def env_float(env: Mapping[str, str], name: str, default: float) -> float:
    """Read a float environment override, keeping *default* when unset/empty."""
    raw = env.get(name)
    return default if raw is None or raw == "" else float(raw)


def env_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read an int environment override, keeping *default* when unset/empty."""
    raw = env.get(name)
    return default if raw is None or raw == "" else int(raw)


def text_extracted(
    text: str,
    fallback_title: str,
    *,
    doc_type: str,
    content_type: str,
    extractor: str,
    metadata: dict[str, Any] | None = None,
) -> Extracted:
    """Build an :class:`Extracted` for a plain-text/markup format."""
    return Extracted(
        doc_type=doc_type,
        title=fallback_title,
        author=None,
        page_count=None,
        full_text=(text or "").strip(),
        content_type=content_type,
        extractor=extractor,
        extractor_version="stdlib",
        metadata=metadata or {},
    )


def _max_zip_uncompressed_bytes() -> int:
    raw = os.environ.get("DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB", "").strip()
    mb = int(raw) if raw else 500
    return mb * 1024 * 1024


def check_zip_bomb_bounds(zf: zipfile.ZipFile) -> None:
    """Reject a zip whose total *uncompressed* size exceeds a sane cap (BL-088).

    DOCX and EPUB are both zip containers with no guard against a classic
    decompression bomb: a small compressed file whose declared uncompressed
    content would exhaust memory before any XML parsing even begins.
    Overridable via ``DOCRAY_EXTRACT_MAX_UNCOMPRESSED_MB`` (default 500 MiB).
    """
    total = sum(info.file_size for info in zf.infolist())
    limit = _max_zip_uncompressed_bytes()
    if total > limit:
        raise ValueError(
            f"archive's uncompressed size ({total} bytes) exceeds the {limit}-byte limit"
        )
