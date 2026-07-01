"""End-to-end tests for the command-line interface (``main``)."""

from __future__ import annotations

import re

import pytest

import extract_phrases as ep


def _make(tmp_path, text, name="doc.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_prints_one_sentence_per_line(tmp_path, capsys):
    p = _make(tmp_path, "First sentence here. Second sentence here.")
    rc = ep.main([str(p), "-q"])
    assert rc == 0
    assert capsys.readouterr().out == "First sentence here.\nSecond sentence here.\n"


def test_output_file_receives_sentences(tmp_path, capsys):
    p = _make(tmp_path, "Alpha one two. Beta three four.")
    out = tmp_path / "out.txt"
    ep.main([str(p), "-o", str(out), "-q"])
    assert out.read_text(encoding="utf-8") == "Alpha one two.\nBeta three four.\n"
    # With -o, nothing is written to stdout.
    assert capsys.readouterr().out == ""


def test_summary_is_printed_to_stderr_by_default(tmp_path, capsys):
    p = _make(tmp_path, "One sentence only here.")
    ep.main([str(p)])
    err = capsys.readouterr().err
    assert "===== Statistics =====" in err
    assert re.search(r"Sentences \(total\) +: 1", err)
    assert "Wrote 1 sentences" in err


def test_quiet_suppresses_summary(tmp_path, capsys):
    p = _make(tmp_path, "One sentence only here.")
    ep.main([str(p), "-q"])
    assert capsys.readouterr().err == ""


def test_unique_deduplicates_preserving_order(tmp_path, capsys):
    p = _make(tmp_path, "Repeat this line. Repeat this line. A different line here.")
    ep.main([str(p), "--unique", "-q"])
    out = capsys.readouterr().out
    assert out == "Repeat this line.\nA different line here.\n"


def test_unique_reports_duplicates_removed(tmp_path, capsys):
    p = _make(tmp_path, "Same thing said. Same thing said.")
    ep.main([str(p), "--unique"])
    err = capsys.readouterr().err
    assert re.search(r"Duplicates +: 1", err)
    assert "Wrote 1 sentences" in err


def test_min_words_filters_short_sentences(tmp_path, capsys):
    p = _make(tmp_path, "Hi. This is a longer sentence to keep.")
    ep.main([str(p), "--min-words", "3", "-q"])
    assert capsys.readouterr().out == "This is a longer sentence to keep.\n"


def test_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        ep.main([str(tmp_path / "nope.txt")])


def test_unsupported_extension_exits(tmp_path):
    p = _make(tmp_path, "x", name="thing.rtf")
    with pytest.raises(SystemExit):
        ep.main([str(p)])


def test_reads_epub_end_to_end(make_epub, capsys):
    path = make_epub({"c1.xhtml": "<p>A whole sentence lives here.</p>"})
    ep.main([str(path), "-q"])
    assert "A whole sentence lives here." in capsys.readouterr().out


def test_paragraphs_flag_separates_groups_with_blank_line(tmp_path, capsys):
    p = _make(tmp_path, "Para one first. Para one second.\n\nPara two only.")
    ep.main([str(p), "--paragraphs", "-q"])
    assert capsys.readouterr().out == (
        "Para one first.\nPara one second.\n\nPara two only.\n"
    )


def test_paragraphs_flag_reports_paragraph_count(tmp_path, capsys):
    p = _make(tmp_path, "One here. Two here.\n\nThree here.")
    ep.main([str(p), "--paragraphs"])
    err = capsys.readouterr().err
    assert re.search(r"Sentences \(total\) +: 3", err)
    assert re.search(r"Paragraphs +: 2", err)
