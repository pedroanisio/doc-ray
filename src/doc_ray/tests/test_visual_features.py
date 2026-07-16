"""Tier-1 deterministic visual features — the ground-truth tier (Prism §3).

PALS posture: this tier is a pure, deterministic function of the crop bytes plus
the pinned tool version — no model, so no hallucination surface. The determinism
test is load-bearing: the proposal's #1 must-fix is that palette quantization be
byte-identical run-to-run (median-cut, not seeded k-means).
"""

from __future__ import annotations

import re
from io import BytesIO

import numpy as np
from PIL import Image

from doc_ray.application.visual_features import (
    ALLOWED_IMAGE_CLASSES,
    TOOL_NAME,
    TOOL_VERSION,
    extract_tier1_features,
    is_meaningful_region,
)

_HEX = re.compile(r"^#[0-9a-f]{6}$")


def _png(color: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (64, 48)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _gradient_png(size: tuple[int, int] = (64, 64)) -> bytes:
    ramp = np.linspace(0, 255, size[0], dtype="uint8")
    arr = np.stack([np.tile(ramp, (size[1], 1))] * 3, axis=-1)
    buf = BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "PNG")
    return buf.getvalue()


def test_features_are_deterministic() -> None:
    data = _gradient_png()
    assert extract_tier1_features(data) == extract_tier1_features(data)


def test_palette_is_wellformed_and_proportions_sum_to_one() -> None:
    f = extract_tier1_features(_gradient_png())
    assert f.dominant_colors
    assert all(_HEX.match(hexv) for hexv, _ in f.dominant_colors)
    assert abs(sum(p for _, p in f.dominant_colors) - 1.0) < 1e-6
    # deterministic ordering: proportion desc, hex asc as tie-break
    assert list(f.dominant_colors) == sorted(f.dominant_colors, key=lambda hp: (-hp[1], hp[0]))


def test_scalar_features_in_range() -> None:
    f = extract_tier1_features(_png())
    for v in (f.mean_saturation, f.mean_value, f.contrast, f.edge_density):
        assert 0.0 <= v <= 1.0
    assert f.image_class in ALLOWED_IMAGE_CLASSES
    assert f.width == 64 and f.height == 48
    assert abs(f.aspect_ratio - 64 / 48) < 1e-6


def test_solid_image_has_near_zero_edge_and_contrast() -> None:
    f = extract_tier1_features(_png(size=(50, 50)))
    assert f.edge_density < 0.01
    assert f.contrast < 0.01
    assert f.image_class != "photo"  # a flat fill is a graphic, never a photo


def test_content_gate_keeps_figures_and_drops_junk() -> None:
    # A real figure (varied content, sane size) is kept.
    assert is_meaningful_region(extract_tier1_features(_gradient_png((80, 80))))
    # A fragment (a side under the minimum) is dropped.
    assert not is_meaningful_region(extract_tier1_features(_gradient_png((32, 32))))
    # A hairline sliver (extreme aspect, both sides >= min) is dropped.
    assert not is_meaningful_region(extract_tier1_features(_gradient_png((640, 48))))
    # A blank/uniform crop is dropped even at a healthy size.
    assert not is_meaningful_region(extract_tier1_features(_png(size=(96, 96))))


def test_histogram_is_per_channel_normalized() -> None:
    f = extract_tier1_features(_gradient_png())
    assert len(f.histogram) == 48  # 16 bins x 3 channels
    assert abs(sum(f.histogram) - 3.0) < 1e-6  # each channel sums to 1.0


def test_tool_identity_is_pinned() -> None:
    assert TOOL_NAME == "doc-ray/visual-features"
    assert TOOL_VERSION == "1.0"
