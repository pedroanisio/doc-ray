"""Shared pytest fixtures for the extract_phrases test-suite.

Makes ``extract_phrases`` importable regardless of where pytest runs, and
provides ``make_epub`` — a factory that writes a minimal, valid EPUB to disk so
the EPUB reader can be exercised without external fixtures.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

# Import paths (src, ., scripts) are configured in pyproject.toml
# [tool.pytest.ini_options] pythonpath, so no sys.path manipulation is needed here.


def _cid(name: str) -> str:
    """Deterministic manifest id derived from a chapter filename."""
    return "id_" + name.replace("/", "_").replace(".", "_")


def _build_epub(
    path: Path,
    chapters: dict[str, str],
    spine: list[str] | None = None,
    *,
    opf_dir: str = "OEBPS",
    include_container: bool = True,
    container_body: str | None = None,
) -> Path:
    """Write a minimal EPUB to *path*.

    ``chapters`` maps a filename (relative to *opf_dir*) to its XHTML body.
    ``spine`` is the reading order; when None, chapters keep dict order. Set
    ``include_container=False`` or pass a broken ``container_body`` to force the
    reader's filename-sorted fallback path.
    """
    opf_prefix = f"{opf_dir}/" if opf_dir else ""
    spine = list(chapters) if spine is None else spine

    manifest_items = "\n".join(
        f'<item id="{_cid(n)}" href="{n}" media-type="application/xhtml+xml"/>' for n in chapters
    )
    spine_items = "\n".join(f'<itemref idref="{_cid(n)}"/>' for n in spine)
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="pub-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Test</dc:title></metadata>\n"
        f"  <manifest>\n{manifest_items}\n</manifest>\n"
        f"  <spine>\n{spine_items}\n</spine>\n"
        "</package>\n"
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        if include_container:
            if container_body is None:
                container_body = (
                    '<?xml version="1.0"?>\n'
                    '<container version="1.0" '
                    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
                    f'  <rootfiles><rootfile full-path="{opf_prefix}content.opf" '
                    'media-type="application/oebps-package+xml"/></rootfiles>\n'
                    "</container>\n"
                )
            zf.writestr("META-INF/container.xml", container_body)
        zf.writestr(f"{opf_prefix}content.opf", opf)
        for name, body in chapters.items():
            zf.writestr(
                f"{opf_prefix}{name}",
                '<?xml version="1.0" encoding="utf-8"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
                f"{body}</body></html>",
            )
    return path


@pytest.fixture
def make_epub(tmp_path):
    """Return a factory: ``make_epub(chapters, spine=..., **kw) -> Path``."""
    counter = {"n": 0}

    def factory(chapters: dict[str, str], spine: list[str] | None = None, **kw) -> Path:
        counter["n"] += 1
        return _build_epub(tmp_path / f"book_{counter['n']}.epub", chapters, spine, **kw)

    return factory
