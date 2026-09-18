"""Adapter: extract embedded rasters from an EPUB container (stdlib zip + PIL).

WHY THIS EXISTS. Prism Tier-1 detected regions only in PDFs: the orchestrator
gated on ``_is_pdf`` and returned ``{"skipped": "not-pdf"}`` for everything else,
with the job recorded as *succeeded*. That silently dropped the entire visual
layer of every EPUB — in a mixed corpus, close to half the documents. A sampled
book carried over a hundred embedded images making up two thirds of its own
archive, and the corpus recorded zero images for it and no error anywhere.

WHY NOT RENDER EPUB PAGES. An EPUB is reflowable: it has no page geometry, so a
rasterizer must first *invent* a layout (page size, font, margins), and the page
numbers and bounding boxes that come out are artefacts of those settings rather
than properties of the document. They would change under a different viewport or
a library upgrade, which is exactly the kind of value this codebase refuses to
store as if it were a fact. So this adapter takes the deterministic path: pull
the rasters the publisher actually embedded, and report NO geometry (``bbox`` and
``xref`` are ``None``) rather than a fabricated one.

WHAT REPLACES THE PAGE NUMBER. ``document_image.page_number`` is ``NOT NULL CHECK
(page_number > 0)``, and a position is genuinely useful for reading order, so
each image is stamped with the 1-based **spine index** of the content document
that references it — chapter position, which IS a property of the EPUB. Images
reachable from no spine document (cover art referenced only from the OPF
metadata) sort after the last spine item, so ordering stays total and stable.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from urllib.parse import unquote

from PIL import Image

from doc_ray.adapters.extraction_common import check_zip_bomb_bounds
from doc_ray.ports.epub_image_source import EpubImage

#: Recorded on every row this adapter produces, so a re-extraction under a newer
#: version is distinguishable from the original pass.
DETECTOR = "doc-ray/epub-embedded-raster"
DETECTOR_VERSION = "1.0"

#: Raster suffixes worth extracting. SVG is deliberately absent: it is vector
#: markup, and rasterizing it needs the same invented layout this adapter avoids.
_RASTER_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff")

#: Below this, a raster is furniture — a spacer, a bullet, a rule — not a figure.
#: The orchestrator applies the real content gate (``is_meaningful_region``) over
#: Tier-1 features; this only avoids decoding thousands of icons to get there.
_MIN_PIXELS = 64

_SRC_RE = re.compile(rb"""(?:src|xlink:href|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def _spine_documents(zf: zipfile.ZipFile) -> list[str]:
    """Content-document paths in reading order, else all (x)html sorted.

    Mirrors ``extraction_markup._epub_spine`` — the same OPF walk the text
    extractor uses, so image spine indices line up with the text this document
    already stores rather than forming a second, divergent notion of order.
    """
    import xml.etree.ElementTree as ET

    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", "replace")
        match = re.search(r'full-path="([^"]+)"', container)
        if match is None:
            raise ValueError("container.xml names no OPF")
        opf_path = match.group(1)
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        ns = {"o": "http://www.idpf.org/2007/opf"}
        manifest = {i.get("id"): i.get("href") for i in opf.iterfind(".//o:manifest/o:item", ns)}
        order = [
            posixpath.normpath(posixpath.join(base, href))
            for ref in opf.iterfind(".//o:spine/o:itemref", ns)
            if (href := manifest.get(ref.get("idref")))
        ]
        if order:
            return order
    except Exception:
        pass
    return sorted(n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))


def _referenced_rasters(zf: zipfile.ZipFile, doc_name: str) -> list[str]:
    """Raster archive entries referenced by one content document, in source order."""
    try:
        markup = zf.read(doc_name)
    except KeyError:
        return []
    base = posixpath.dirname(doc_name)
    names = set(zf.namelist())
    out: list[str] = []
    for raw in _SRC_RE.findall(markup):
        href = unquote(raw.decode("utf-8", "replace")).split("#", 1)[0]
        if not href.lower().endswith(_RASTER_SUFFIXES):
            continue
        resolved = posixpath.normpath(posixpath.join(base, href))
        if resolved in names and resolved not in out:
            out.append(resolved)
    return out


def _decode_to_png(data: bytes) -> tuple[bytes, int, int] | None:
    """Re-encode a raster as PNG; ``None`` when it is undecodable or furniture."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            width, height = img.size
            if width * height < _MIN_PIXELS:
                return None
            # Flatten palette/alpha to RGB: Tier-1 features (histogram, saturation,
            # edge density) are defined over colour channels, and a mode-dependent
            # feature vector would make identical images compare unequal.
            converted = img.convert("RGB")
            buf = io.BytesIO()
            converted.save(buf, format="PNG", optimize=False)
            return buf.getvalue(), width, height
    except Exception:
        return None


class ZipEpubImageSource:
    """Extract embedded rasters from EPUB bytes, ordered by spine position."""

    def embedded_images(self, epub_bytes: bytes) -> list[EpubImage]:
        """Return every decodable embedded raster, in reading order.

        Each raster is returned once, at the position of the FIRST spine document
        that references it: a logo repeated in every chapter is one figure, not
        forty, and the content-addressed store would collapse the duplicates
        anyway after paying to decode each one.

        Args:
            epub_bytes: Raw EPUB (ZIP) bytes.

        Returns:
            One :class:`EpubImage` per distinct raster, spine order then source
            order within a document, with unreferenced rasters last.
        """
        out: list[EpubImage] = []
        with zipfile.ZipFile(io.BytesIO(epub_bytes)) as zf:
            check_zip_bomb_bounds(zf)
            spine = _spine_documents(zf)
            seen: set[str] = set()

            def _take(name: str, spine_index: int) -> None:
                if name in seen:
                    return
                seen.add(name)
                try:
                    data = zf.read(name)
                except KeyError:
                    return
                decoded = _decode_to_png(data)
                if decoded is None:
                    return
                png, width, height = decoded
                out.append(EpubImage(spine_index, name, png, width, height))

            for index, doc_name in enumerate(spine, start=1):
                for name in _referenced_rasters(zf, doc_name):
                    _take(name, index)

            # Cover art is often referenced only from OPF metadata, never from a
            # spine document — the single most valuable image in the book. Sweep
            # the remaining rasters after the last spine position so nothing that
            # the publisher shipped is dropped for want of an <img> tag.
            trailing = len(spine) + 1
            for name in sorted(zf.namelist()):
                if name.lower().endswith(_RASTER_SUFFIXES):
                    _take(name, trailing)
        return out
