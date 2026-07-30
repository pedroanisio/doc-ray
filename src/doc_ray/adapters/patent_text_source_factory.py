"""Build the configured :class:`PatentTextSource`, or explain its absence.

One place decides whether the patent path is available, so the pipeline never
imports the optional library and never has to reason about deployment state.

Absence is reported, never silently substituted: falling back to the ordinary
PDF extractor would emit column-interleaved text that reads as a successful
ingest, which is the exact failure this path exists to remove.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from doc_ray.ports.patent_text_source import PatentTextSource

#: Raster resolution for the column analysis. 300 is the library's own default
#: and what its published recall figures were measured at; lowering it changes
#: results, so it is an explicit knob rather than a silent constant.
_DEFAULT_DPI = 300


def patent_extraction_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether operators have turned the patent path on.

    Default OFF. The path costs a full rasterisation pass per page and pulls
    Tesseract plus Ghostscript into the runtime, so a deployment opts in rather
    than discovering the cost when someone ticks a checkbox.
    """
    source = os.environ if env is None else env
    raw = source.get("DOCRAY_PATENT_EXTRACTION", "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def patent_dpi_from_env(env: Mapping[str, str] | None = None) -> int:
    """Return the raster DPI handed to the column analysis."""
    source = os.environ if env is None else env
    raw = source.get("DOCRAY_PATENT_OCR_DPI", "300").strip()
    try:
        dpi = int(raw)
    except ValueError:
        # An unreadable value must not fail an ingest that was otherwise fine;
        # the measured default is the safe reading, not a crash.
        return _DEFAULT_DPI
    return dpi if dpi > 0 else _DEFAULT_DPI


def build_patent_source(env: Mapping[str, str] | None = None) -> PatentTextSource | None:
    """Return the configured source, or ``None`` when the path is switched off.

    Returning ``None`` means "not enabled here" and is answered upstream with a
    caller error. It does not mean "installed and broken" — that surfaces later
    as :class:`PatentTextUnavailableError` from the adapter, which names the
    missing piece.
    """
    if not patent_extraction_enabled(env):
        return None
    from doc_ray.adapters.patent_text_source_patentocr import PatentOcrTextSource

    return PatentOcrTextSource(dpi=patent_dpi_from_env(env))
