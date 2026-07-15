#!/usr/bin/env python3
"""Generate the webapp favicon set from the doc·ray brand master.

Source of truth is the brand icon (the white ``d·`` node on brand red — see
docs/branding/doc-ray-branding-brief.md § "The icon / app mark"). This derives
the small, per-platform assets the browser and OSes actually load, so the 1.25k²
master is never shipped to a tab. Re-run after the master changes:

    .venv/bin/python scripts/generate_favicons.py

Idempotent: it overwrites the derived files in webapp/static/ in place.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

_ROOT = Path(__file__).resolve().parents[1]
_MASTER = _ROOT / "static" / "images" / "icon.png"
_OUT = _ROOT / "webapp" / "static"

#: (filename, edge px) for the standalone PNG variants the <link> tags reference.
_PNG_VARIANTS: tuple[tuple[str, int], ...] = (
    ("favicon-16.png", 16),
    ("favicon-32.png", 32),
    ("favicon-48.png", 48),
    ("apple-touch-icon.png", 180),  # iOS home screen
    ("icon-192.png", 192),  # PWA manifest
    ("icon-512.png", 512),  # PWA manifest / install
)
#: Sizes packed into the multi-resolution favicon.ico (legacy + default request).
_ICO_SIZES: tuple[tuple[int, int], ...] = ((16, 16), (32, 32), (48, 48))


def _load_master() -> Image.Image:
    if not _MASTER.exists():
        raise SystemExit(f"brand master not found: {_MASTER}")
    return Image.open(_MASTER).convert("RGBA")


def generate() -> list[Path]:
    """Write every derived favicon asset; return the paths written."""
    master = _load_master()
    _OUT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for name, edge in _PNG_VARIANTS:
        resized = master.resize((edge, edge), Image.LANCZOS)
        dest = _OUT / name
        # apple-touch icons must be opaque (iOS ignores/handles alpha poorly).
        if name == "apple-touch-icon.png":
            resized = resized.convert("RGB")
        resized.save(dest)
        written.append(dest)

    ico = _OUT / "favicon.ico"
    master.save(ico, format="ICO", sizes=_ICO_SIZES)
    written.append(ico)
    return written


if __name__ == "__main__":
    for path in generate():
        print(f"wrote {path.relative_to(_ROOT)}")
