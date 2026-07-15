"""The doc·ray favicon set is served and wired into every page's <head>.

Covers the brand-icon wiring: base.html references the versioned favicon/
apple-touch/manifest links, the root /favicon.ico request resolves (browsers
ask for it regardless of the <link> tags), and the generator produces the
small per-platform assets from the brand master.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from webapp import app as webapp

_STATIC = Path(webapp.app.static_folder or "")


def test_head_links_the_versioned_favicon_set() -> None:
    body = webapp.app.test_client().get("/").get_data(as_text=True)
    assert 'rel="icon"' in body and "favicon.ico?v=" in body
    assert 'sizes="32x32"' in body and "favicon-32.png?v=" in body
    assert 'rel="apple-touch-icon"' in body and "apple-touch-icon.png?v=" in body
    assert 'rel="manifest"' in body and "site.webmanifest?v=" in body
    assert 'name="theme-color"' in body


def test_root_favicon_ico_resolves() -> None:
    resp = webapp.app.test_client().get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.mimetype in {"image/vnd.microsoft.icon", "image/x-icon"}


def test_manifest_is_served_and_declares_install_icons() -> None:
    resp = webapp.app.test_client().get("/static/site.webmanifest")
    assert resp.status_code == 200
    data = resp.get_json(force=True)
    assert data["name"] == "doc·ray"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_generated_favicons_are_small_and_correctly_sized() -> None:
    with Image.open(_STATIC / "favicon-32.png") as img:
        assert img.size == (32, 32)
    with Image.open(_STATIC / "apple-touch-icon.png") as img:
        assert img.size == (180, 180)
    # A tab icon must stay tiny — the 1.25k² master must never ship as the favicon.
    assert (_STATIC / "favicon-32.png").stat().st_size < 10_000


def test_only_the_outer_background_is_transparent() -> None:
    # The outer light margin is cleared to transparent on all four sides, while
    # the red tile and the inner white d· mark stay opaque (the inner white is
    # enclosed by red, so the flood fill never reaches it).
    with Image.open(_STATIC / "icon-512.png").convert("RGBA") as img:
        w, h = img.size
        for probe in [(0, 0), (w // 2, 2), (2, h // 2), (w // 2, h - 3)]:
            assert img.getpixel(probe)[3] == 0, f"outer margin not transparent at {probe}"
        assert img.getpixel((w // 2, h // 2))[3] == 255  # inner mark stays opaque


def test_apple_touch_icon_stays_opaque() -> None:
    # iOS renders alpha as black; the apple-touch icon must remain opaque.
    with Image.open(_STATIC / "apple-touch-icon.png") as img:
        assert img.mode == "RGB"
