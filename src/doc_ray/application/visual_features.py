"""Tier-1 deterministic visual features (Prism §3, Tier 1 — ground truth).

A pure, deterministic function of the crop bytes plus a pinned tool version:
colour palette (hex + measured proportions), per-channel histogram, mean
saturation/value, RMS contrast, edge density, a rule-based image-type class, and
EXIF when present. No model is involved, so there is no hallucination surface —
this tier is the reference against which Tiers 2/3 are judged.

Determinism is the load-bearing property (the proposal's #1 must-fix): the
palette uses **median-cut** quantization (deterministic), never seeded k-means.
The guarantee is "deterministic given the pinned quantizer/tool version", which
``TOOL_VERSION`` records. See ``test_visual_features.py`` for the byte-identical
fixture assertion.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import numpy as np
from PIL import ExifTags, Image

#: Tool identity, recorded as provenance on every produced row. Bump the version
#: whenever the quantizer, bin count, thresholds, or classifier rules change — the
#: determinism guarantee holds only *given* a pinned tool version.
TOOL_NAME = "doc-ray/visual-features"
TOOL_VERSION = "1.0"

#: The image-type classes the deterministic classifier may emit (also the DB CHECK
#: set for ``image_tier1_features.image_class``).
ALLOWED_IMAGE_CLASSES = ("photo", "diagram", "screenshot", "line-art", "chart")

_PALETTE_COLORS = 6
_HIST_BINS = 16
#: Luminance gradient (fraction of full scale) at/above which a pixel is an edge.
_EDGE_THRESHOLD = 0.08
_LUMA = np.array([0.299, 0.587, 0.114])

# --- Content-quality gate (drop non-content crops before they are stored) -----
# A large fraction of raw embedded rasters are page-background slivers, rules,
# tiny logos, and blank pages — not figures. These thresholds were calibrated on
# the corpus so they drop that junk while sparing real figures AND sparse scanned
# pages (which still carry text-level contrast). Applied in ``visual_extract``.
#: Minimum length of each side (px) — below this a crop is a fragment/sliver.
MIN_MEANINGFUL_SIDE_PX = 48
#: Maximum side ratio — a rule/underline is far thinner than any real figure.
MAX_MEANINGFUL_ASPECT = 12.0
#: A crop this uniform (near-zero RMS contrast) AND this edge-free is blank.
BLANK_CONTRAST_MAX = 0.02
BLANK_EDGE_MAX = 0.01


@dataclass(frozen=True)
class Tier1Features:
    """Deterministic visual features for one image/region."""

    dominant_colors: tuple[tuple[str, float], ...]
    histogram: tuple[float, ...]
    mean_saturation: float
    mean_value: float
    contrast: float
    edge_density: float
    image_class: str
    aspect_ratio: float
    width: int
    height: int
    exif: dict[str, str] | None

    def palette_json(self) -> list[dict[str, Any]]:
        """Palette as ``[{hex, proportion}]`` for the ``dominant_colors`` jsonb column."""
        return [{"hex": hexv, "proportion": prop} for hexv, prop in self.dominant_colors]


def is_meaningful_region(features: Tier1Features) -> bool:
    """Whether a crop carries real visual content (vs. a sliver/rule/blank page).

    Deterministic gate over already-computed Tier-1 features, so no extra decode:
    reject fragments (a side under ``MIN_MEANINGFUL_SIDE_PX``), hairline
    slivers/rules (aspect over ``MAX_MEANINGFUL_ASPECT``), and blank crops
    (near-zero contrast AND edge density). Sparse scanned pages keep text-level
    contrast, so they pass; real figures have edges, so they pass.
    """
    if min(features.width, features.height) < MIN_MEANINGFUL_SIDE_PX:
        return False
    shortest = min(features.width, features.height)
    if shortest and max(features.width, features.height) / shortest > MAX_MEANINGFUL_ASPECT:
        return False
    return not (features.contrast < BLANK_CONTRAST_MAX and features.edge_density < BLANK_EDGE_MAX)


def _dominant_colors(rgb: Image.Image) -> tuple[tuple[str, float], ...]:
    """Return ``(hex, proportion)`` pairs via deterministic median-cut quantization."""
    quant = rgb.quantize(
        colors=_PALETTE_COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    palette = quant.getpalette() or []
    counts = np.bincount(np.asarray(quant, dtype=np.int64).ravel(), minlength=_PALETTE_COLORS)
    total = int(counts.sum()) or 1
    merged: dict[str, float] = {}
    for index in range(len(counts)):
        count = int(counts[index])
        if count == 0:
            continue
        r, g, b = palette[index * 3 : index * 3 + 3]
        hexv = f"#{r:02x}{g:02x}{b:02x}"
        merged[hexv] = merged.get(hexv, 0.0) + count / total
    return tuple(sorted(merged.items(), key=lambda hp: (-hp[1], hp[0])))


def _histogram(arr: np.ndarray) -> tuple[float, ...]:
    """Return a per-channel, per-channel-normalized 16-bin histogram (48 values)."""
    out: list[float] = []
    for channel in range(3):
        hist, _ = np.histogram(arr[..., channel], bins=_HIST_BINS, range=(0.0, 255.0))
        denom = hist.sum() or 1
        out.extend((hist / denom).tolist())
    return tuple(out)


def _edge_density(luma: np.ndarray) -> float:
    """Fraction of adjacent-pixel luminance steps that exceed the edge threshold."""
    grad_x = np.abs(np.diff(luma, axis=1)) / 255.0
    grad_y = np.abs(np.diff(luma, axis=0)) / 255.0
    edges = int((grad_x > _EDGE_THRESHOLD).sum() + (grad_y > _EDGE_THRESHOLD).sum())
    total = grad_x.size + grad_y.size
    return float(edges / total) if total else 0.0


def _classify(n_colors: int, mean_saturation: float, contrast: float, edge_density: float) -> str:
    """Rule-based, deterministic image-type class.

    Heuristic (to be calibrated with labelled fixtures per Prism Phase 1), but
    deterministic and defensible for the clear cases: a flat fill is a graphic,
    sharp desaturated line work is line-art, etc.
    """
    if edge_density < 0.005 and contrast < 0.02:
        return "diagram"  # flat / near-uniform region
    if mean_saturation < 0.15 and edge_density > 0.10:
        return "line-art"
    if mean_saturation < 0.20 and edge_density > 0.04:
        return "screenshot"
    if edge_density > 0.08 and n_colors <= 4:
        return "chart"
    return "photo"


def _exif(img: Image.Image) -> dict[str, str] | None:
    """Return stringified EXIF tags when present (mostly standalone photos), else None."""
    raw = img.getexif()
    if not raw:
        return None
    out = {ExifTags.TAGS.get(tag_id, str(tag_id)): str(value) for tag_id, value in raw.items()}
    return out or None


def extract_tier1_features(image_bytes: bytes) -> Tier1Features:
    """Compute deterministic Tier-1 features for one image crop."""
    with Image.open(BytesIO(image_bytes)) as img:
        img.load()
        exif = _exif(img)
        rgb = img.convert("RGB")
    width, height = rgb.size
    arr = np.asarray(rgb, dtype=np.float64)
    hsv = np.asarray(rgb.convert("HSV"), dtype=np.float64)
    luma = arr @ _LUMA

    dominant = _dominant_colors(rgb)
    mean_saturation = float(hsv[..., 1].mean() / 255.0)
    mean_value = float(hsv[..., 2].mean() / 255.0)
    contrast = float(luma.std() / 255.0)
    edge_density = _edge_density(luma)
    return Tier1Features(
        dominant_colors=dominant,
        histogram=_histogram(arr),
        mean_saturation=mean_saturation,
        mean_value=mean_value,
        contrast=contrast,
        edge_density=edge_density,
        image_class=_classify(len(dominant), mean_saturation, contrast, edge_density),
        aspect_ratio=float(width / height) if height else 0.0,
        width=width,
        height=height,
        exif=exif,
    )
