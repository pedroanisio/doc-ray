"""Tests for EPUB embedded-raster extraction.

REGRESSION CONTEXT. Prism Tier-1 gated on ``_is_pdf`` and returned
``{"skipped": "not-pdf"}`` for every other format while recording the job as
*succeeded*. That left every EPUB — close to half of a mixed corpus — with a
completely empty visual layer and no error anywhere to show it. One sampled book
carried over a hundred embedded rasters, two thirds of its own archive, and the
corpus said it had zero images.

These tests build real EPUB containers (zip + container.xml + OPF + XHTML + real
encoded rasters) rather than mocking the reader, because the bug was never in the
extraction arithmetic — it was in which containers the pipeline would open at all.
"""

from __future__ import annotations

import io
import zipfile

from PIL import Image

from doc_ray.adapters.epub_image_source_zip import ZipEpubImageSource

CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""


def _raster(fmt: str = "PNG", size: tuple[int, int] = (40, 30), colour: str = "red") -> bytes:
    """A real encoded raster — decoding is part of what is under test."""
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format=fmt)
    return buf.getvalue()


def _opf(spine_items: list[str], extra_manifest: list[tuple[str, str]] | None = None) -> str:
    manifest = "\n".join(
        f'<item id="doc{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, href in enumerate(spine_items)
    )
    for item_id, href in extra_manifest or []:
        manifest += f'\n<item id="{item_id}" href="{href}" media-type="image/png"/>'
    spine = "\n".join(f'<itemref idref="doc{i}"/>' for i in range(len(spine_items)))
    return f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>"""


def _epub(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        for name, payload in files.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _book(
    chapters: dict[str, str],
    images: dict[str, bytes],
    extra_manifest: list[tuple[str, str]] | None = None,
) -> bytes:
    files: dict[str, bytes | str] = {
        "OEBPS/content.opf": _opf([n.removeprefix("OEBPS/") for n in chapters], extra_manifest),
    }
    files.update(chapters)
    files.update(images)
    return _epub(files)


def test_extracts_a_raster_referenced_by_a_chapter():
    book = _book(
        {"OEBPS/c1.xhtml": '<html><body><img src="img/fig1.png"/></body></html>'},
        {"OEBPS/img/fig1.png": _raster()},
    )

    images = ZipEpubImageSource().embedded_images(book)

    assert len(images) == 1
    assert images[0].archive_name == "OEBPS/img/fig1.png"
    assert (images[0].width_px, images[0].height_px) == (40, 30)


def test_spine_index_records_reading_order_not_archive_order():
    # The image in the SECOND chapter must carry spine index 2, even though its
    # archive name sorts first — position is the point of the column.
    book = _book(
        {
            "OEBPS/c1.xhtml": '<html><body><img src="zzz.png"/></body></html>',
            "OEBPS/c2.xhtml": '<html><body><img src="aaa.png"/></body></html>',
        },
        {"OEBPS/zzz.png": _raster(colour="blue"), "OEBPS/aaa.png": _raster(colour="green")},
    )

    by_name = {i.archive_name: i.spine_index for i in ZipEpubImageSource().embedded_images(book)}

    assert by_name["OEBPS/zzz.png"] == 1
    assert by_name["OEBPS/aaa.png"] == 2


def test_cover_referenced_only_from_the_opf_is_still_extracted():
    # Cover art is often referenced by no spine document at all. It is usually the
    # single most valuable image in the book, so it must not be dropped for want of
    # an <img> tag; it sorts after the last spine position.
    book = _book(
        {"OEBPS/c1.xhtml": "<html><body><p>no images here</p></body></html>"},
        {"OEBPS/cover.png": _raster(colour="black")},
        extra_manifest=[("cover-img", "cover.png")],
    )

    images = ZipEpubImageSource().embedded_images(book)

    assert [i.archive_name for i in images] == ["OEBPS/cover.png"]
    assert images[0].spine_index == 2  # one spine doc, so trailing position is 2


def test_a_raster_reused_across_chapters_is_returned_once():
    # A logo repeated in every chapter is one figure, not forty.
    book = _book(
        {
            "OEBPS/c1.xhtml": '<html><body><img src="logo.png"/></body></html>',
            "OEBPS/c2.xhtml": '<html><body><img src="logo.png"/></body></html>',
            "OEBPS/c3.xhtml": '<html><body><img src="logo.png"/></body></html>',
        },
        {"OEBPS/logo.png": _raster()},
    )

    images = ZipEpubImageSource().embedded_images(book)

    assert len(images) == 1
    assert images[0].spine_index == 1  # first reference wins


def test_jpeg_input_is_re_encoded_as_png():
    # Downstream Tier-1 featurizing is defined over PNG bytes, so the adapter must
    # normalise the container regardless of what the publisher shipped.
    book = _book(
        {"OEBPS/c1.xhtml": '<html><body><img src="photo.jpg"/></body></html>'},
        {"OEBPS/photo.jpg": _raster(fmt="JPEG", size=(64, 64))},
    )

    images = ZipEpubImageSource().embedded_images(book)

    assert len(images) == 1
    assert images[0].png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_svg_is_not_treated_as_a_raster():
    # SVG is vector markup; rasterizing it needs an invented layout, which is
    # exactly what this path refuses to do.
    book = _book(
        {
            "OEBPS/c1.xhtml": '<html><body><img src="diagram.svg"/></body></html>',
        },
        {"OEBPS/diagram.svg": b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'},
    )

    assert ZipEpubImageSource().embedded_images(book) == []


def test_undecodable_bytes_are_skipped_not_raised():
    # A truncated or corrupt raster must not fail the whole document's extraction.
    book = _book(
        {
            "OEBPS/c1.xhtml": (
                '<html><body><img src="broken.png"/><img src="good.png"/></body></html>'
            )
        },
        {"OEBPS/broken.png": b"not an image at all", "OEBPS/good.png": _raster()},
    )

    images = ZipEpubImageSource().embedded_images(book)

    assert [i.archive_name for i in images] == ["OEBPS/good.png"]


def test_tiny_furniture_rasters_are_skipped():
    # 4x4 spacers and bullets are not figures; decoding thousands of them to reach
    # the content gate is wasted work.
    book = _book(
        {"OEBPS/c1.xhtml": '<html><body><img src="spacer.png"/></body></html>'},
        {"OEBPS/spacer.png": _raster(size=(4, 4))},
    )

    assert ZipEpubImageSource().embedded_images(book) == []


def test_extraction_is_deterministic_across_runs():
    # Two passes over the same bytes must produce the same rows in the same order,
    # or re-extraction would churn content-addressed keys.
    book = _book(
        {
            "OEBPS/c1.xhtml": '<html><body><img src="a.png"/><img src="b.png"/></body></html>',
            "OEBPS/c2.xhtml": '<html><body><img src="c.png"/></body></html>',
        },
        {
            "OEBPS/a.png": _raster(colour="red"),
            "OEBPS/b.png": _raster(colour="blue"),
            "OEBPS/c.png": _raster(colour="green"),
        },
    )
    source = ZipEpubImageSource()

    first = [(i.spine_index, i.archive_name, i.png_bytes) for i in source.embedded_images(book)]
    second = [(i.spine_index, i.archive_name, i.png_bytes) for i in source.embedded_images(book)]

    assert first == second
    assert [n for _, n, _ in first] == ["OEBPS/a.png", "OEBPS/b.png", "OEBPS/c.png"]


def test_epub_with_no_spine_falls_back_to_sorted_html():
    # A malformed OPF must degrade to "read every (x)html" rather than yield
    # nothing — the failure mode this whole fix exists to eliminate.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", "<not-valid-xml>")
        zf.writestr("c1.xhtml", '<html><body><img src="fig.png"/></body></html>')
        zf.writestr("fig.png", _raster())

    images = ZipEpubImageSource().embedded_images(buf.getvalue())

    assert [i.archive_name for i in images] == ["fig.png"]


def test_xlink_href_references_are_followed():
    # SVG <image xlink:href> wrapping a raster is common in converted EPUBs.
    book = _book(
        {"OEBPS/c1.xhtml": ('<html><body><svg><image xlink:href="scan.png"/></svg></body></html>')},
        {"OEBPS/scan.png": _raster()},
    )

    assert [i.archive_name for i in ZipEpubImageSource().embedded_images(book)] == [
        "OEBPS/scan.png"
    ]
