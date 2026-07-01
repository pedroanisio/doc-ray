"""CLI-surface tests for the heading-extraction prototype.

Exercises `headings.main(argv)` and `headings.run(path, debug)` end-to-end
against real PDFs written to disk (main/run take filesystem paths, so the
canonical *_pdf fixtures — which are already-built path strings usable only
via extract_headings — are intentionally NOT used here; we build our own
inputs with synthpdf.build_pdf + tmp_path).

Assertions target concrete printed content and concrete return codes so a
broken CLI (wrong rc, missing output, dir not walked, debug flag ignored)
would fail these tests rather than pass vacuously.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fitz
import headings
from synthpdf import L, PdfSpec, build_pdf, body_block, heading


# --- helpers ---------------------------------------------------------------

def _numbered_pdf(path: str) -> str:
    """A heuristic (no embedded TOC) PDF with two bold numbered headings."""
    lines = (
        body_block(3)
        + [heading("1 Introduction")] + body_block(4)
        + [heading("2 Methods")] + body_block(4)
    )
    return build_pdf(path, PdfSpec(lines=lines))


def _single_heading_pdf(path: str, title: str) -> str:
    lines = body_block(3) + [heading(title)] + body_block(4)
    return build_pdf(path, PdfSpec(lines=lines))


def _toc_pdf(path: str) -> str:
    """A PDF carrying an embedded multi-level outline (TOC fast-path)."""
    lines = (
        body_block(3)
        + [L("Chapter One", 18, True, new_page=True)] + body_block(4)
        + [L("Section 1.1", 14, True, new_page=True)] + body_block(4)
    )
    toc = [[1, "Chapter One", 2], [2, "Section 1.1", 3]]
    return build_pdf(path, PdfSpec(lines=lines, toc=toc))


# --- main([file]) ----------------------------------------------------------

def test_main_single_file_returns_zero_and_prints_heading(tmp_path, capsys):
    pdf = _numbered_pdf(str(tmp_path / "alpha.pdf"))

    rc = headings.main([pdf])

    out = capsys.readouterr().out
    assert rc == 0
    # The two real headings must appear in the printed outline.
    assert "1 Introduction" in out
    assert "2 Methods" in out
    # The banner reports the filename, page count and heuristic source.
    assert "alpha.pdf" in out
    assert "source=heuristic" in out
    assert "2 headings" in out


def test_main_nondebug_omits_confidence_annotations(tmp_path, capsys):
    """Without --debug the plain outline is printed: no 'conf=' / '#' markers."""
    pdf = _numbered_pdf(str(tmp_path / "plain.pdf"))

    rc = headings.main([pdf])

    out = capsys.readouterr().out
    assert rc == 0
    assert "1 Introduction" in out
    # Plain mode: no per-heading debug annotations and no markdown '#' prefixes.
    assert "conf=" not in out
    # Banner uses '=' rules but heading rows themselves carry no leading '#'.
    assert "\n# " not in out


# --- main([--debug file]) --------------------------------------------------

def test_main_debug_prints_confidence_and_signal_annotations(tmp_path, capsys):
    pdf = _numbered_pdf(str(tmp_path / "dbg.pdf"))

    rc = headings.main([pdf, "--debug"])

    out = capsys.readouterr().out
    assert rc == 0
    # Debug annotations expose confidence and the boolean signals per heading.
    assert "conf=0.9" in out
    # Heuristic numbered headings fire the bold/short/num signals.
    assert "bold" in out and "short" in out and "num" in out
    # Debug rows are markdown-style with a page reference.
    assert "# 1 Introduction" in out
    assert "(p1)" in out


# --- main([dir]) processes every PDF in the directory ----------------------

def test_main_directory_processes_both_pdfs(tmp_path, capsys):
    _numbered_pdf(str(tmp_path / "alpha.pdf"))
    _single_heading_pdf(str(tmp_path / "beta.pdf"), "1 Only Heading")

    rc = headings.main([str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 0
    # Both files must be walked and reported.
    assert "alpha.pdf" in out
    assert "beta.pdf" in out
    # Content from each file appears.
    assert "1 Introduction" in out          # from alpha
    assert "1 Only Heading" in out          # from beta


def test_main_empty_directory_returns_zero_no_output(tmp_path, capsys):
    """A directory with no PDFs yields no targets: rc 0 and nothing printed."""
    empty = tmp_path / "empty"
    empty.mkdir()

    rc = headings.main([str(empty)])

    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


# --- empty argv is a usage error -------------------------------------------

def test_main_empty_argv_returns_one_and_prints_usage(capsys):
    rc = headings.main([])

    out = capsys.readouterr().out
    assert rc == 1
    assert "usage:" in out
    assert "headings.py" in out


def test_main_only_flags_no_path_returns_one(capsys):
    """`--debug` alone leaves no positional target -> usage error (rc 1)."""
    rc = headings.main(["--debug"])

    out = capsys.readouterr().out
    assert rc == 1
    assert "usage:" in out


# --- nonexistent path handling ---------------------------------------------

def test_main_nonexistent_nonpdf_path_is_ignored(tmp_path, capsys):
    """A missing path without a .pdf suffix is filtered out (no run(), rc 0)."""
    missing = tmp_path / "nope.txt"          # never created

    rc = headings.main([str(missing)])

    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_main_nonexistent_pdf_raises_filenotfound(tmp_path):
    """A missing *.pdf still reaches run()->fitz.open, which raises.

    NOTE: possible bug: the CLI spec says a nonexistent path should be
    "handled gracefully (no traceback)", but main() filters targets only by
    the '.pdf' suffix and never checks existence, so a missing file whose
    name ends in '.pdf' reaches run()->fitz.open and propagates
    fitz.FileNotFoundError out of main() with a traceback. (Note fitz's
    FileNotFoundError subclasses RuntimeError, NOT the builtin.) Asserting the
    CURRENT (raising) behavior.
    """
    missing = tmp_path / "nope.pdf"          # never created

    with pytest.raises(fitz.FileNotFoundError) as excinfo:
        headings.main([str(missing)])
    assert "no such file" in str(excinfo.value)
    # It is the pymupdf error (a RuntimeError), not the builtin OSError family.
    assert not isinstance(excinfo.value, OSError)


# --- run(Path, debug=...) directly -----------------------------------------

def test_run_debug_prints_confidence_annotations(tmp_path, capsys):
    pdf = _numbered_pdf(str(tmp_path / "run_dbg.pdf"))

    headings.run(Path(pdf), debug=True)

    out = capsys.readouterr().out
    assert "conf=" in out
    assert "1 Introduction" in out
    assert "source=heuristic" in out
    # Debug row is markdown-styled with a page reference.
    assert "(p1)" in out


def test_run_nondebug_has_no_confidence_annotations(tmp_path, capsys):
    pdf = _numbered_pdf(str(tmp_path / "run_plain.pdf"))

    headings.run(Path(pdf), debug=False)

    out = capsys.readouterr().out
    assert "conf=" not in out
    # Plain outline still prints the heading text and banner.
    assert "1 Introduction" in out
    assert "source=heuristic" in out


def test_run_toc_source_never_annotates_even_in_debug(tmp_path, capsys):
    """The TOC fast-path is authoritative: run() labels source=toc and, even
    with debug=True, suppresses the per-heading 'conf=' annotation."""
    pdf = _toc_pdf(str(tmp_path / "toc.pdf"))

    headings.run(Path(pdf), debug=True)

    out = capsys.readouterr().out
    assert "source=toc" in out
    assert "Chapter One" in out
    assert "Section 1.1" in out
    # source == 'toc' => the conf annotation is deliberately withheld.
    assert "conf=" not in out
