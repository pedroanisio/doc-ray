"""Tests for the lossless hierarchical document model (docmodel)."""

from __future__ import annotations

import json

import docmodel as dm
import pytest

# ---------------------------------------------------------------------------
# The core guarantee: compose(decompose(t)) == t
# ---------------------------------------------------------------------------

ROUNDTRIP_CASES = [
    "",
    " ",
    "\n\n\n",
    "word",
    "Hello world.",
    "Dr. Silva went home. He slept.",
    "A, B; C: D. Next one!",
    "Line one\nline two\n\nPara two.",
    "  leading and trailing.  ",
    "Título\n\nParágrafo com ação, coração.",
    "1.5 and 3.14 are numbers. J. R. Smith too.",
    "Tabs\there\tand\r\nCRLF\r\n\r\nsecond.",
    "# Heading\n\nBody text here. More.",
    "1.6.9 Systems\n\nThe probe works.",
    "!!!???...",
    "no ending punctuation or newline",
    "trailing newline.\n",
    "emoji 🚀 and sym § ¶ ok.",
    "Multiple    internal     spaces kept.",
    "Mixed\n\n\n\n  whitespace\t\tblocks.",
]


@pytest.mark.parametrize("text", ROUNDTRIP_CASES)
def test_compose_is_exact_inverse_of_decompose(text):
    assert dm.compose(dm.decompose(text)) == text


@pytest.mark.parametrize("text", ROUNDTRIP_CASES)
def test_every_internal_node_text_equals_join_of_children(text):
    # The structural invariant that makes losslessness hold at every level.
    def check(node):
        if node.children:
            assert node.text == "".join(c.compose() for c in node.children)
            for c in node.children:
                check(c)

    check(dm.decompose(text))


# ---------------------------------------------------------------------------
# JSON serialization round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ROUNDTRIP_CASES)
def test_json_dict_round_trip_is_lossless(text):
    tree = dm.decompose(text)
    restored = dm.Node.from_dict(json.loads(json.dumps(tree.to_dict())))
    assert dm.compose(restored) == text


def test_leaf_dicts_carry_text_internal_dicts_carry_children():
    d = dm.decompose("Hi there.").to_dict()
    assert d["kind"] == "document"
    assert "children" in d and "text" not in d
    # Drill to a leaf and confirm it stores text, not children.
    node = d
    while "children" in node:
        node = node["children"][0]
    assert "text" in node and "children" not in node


# ---------------------------------------------------------------------------
# Structure of the decomposition
# ---------------------------------------------------------------------------


def test_hierarchy_kinds_are_present():
    tree = dm.decompose("Intro heading\n\nA sentence, with a clause. Second one.")
    kinds = set(dm.counts(tree))
    assert {"document", "section", "paragraph", "sentence", "phrase", "word"} <= kinds


def test_sentences_split_on_terminal_punctuation():
    tree = dm.decompose("First sentence here. Second sentence here.")
    sentences = list(tree.iter_kind("sentence"))
    assert len(sentences) == 2
    assert sentences[0].compose() == "First sentence here."


def test_abbreviation_does_not_start_a_new_sentence():
    tree = dm.decompose("Dr. Silva arrived. He waited.")
    assert len(list(tree.iter_kind("sentence"))) == 2


def test_phrases_split_on_clause_punctuation():
    tree = dm.decompose("Alpha, beta, and gamma end here.")
    phrases = [p.compose() for p in tree.iter_kind("phrase")]
    assert "Alpha" in phrases[0]
    assert len(phrases) >= 3  # split on the two commas


def test_words_are_extracted_as_leaves():
    tree = dm.decompose("The quick brown fox.")
    words = [w.compose() for w in tree.iter_kind("word")]
    assert words == ["The", "quick", "brown", "fox"]


@pytest.mark.parametrize(
    "block, expected",
    [
        ("1.6.9 Systems function", "heading"),  # dotted-decimal section number
        ("# Title", "heading"),  # markdown marker
        ("CONSTITUIÇÃO", "heading"),  # short, titley
        ("This is a normal sentence that ends.", "paragraph"),
        ("1. Os Estados Partes se comprometem a assegurar.", "paragraph"),  # numbered prose
        ("XXX – é garantido o direito;", "paragraph"),  # roman list item + clause end
        ("• a bullet point here", "paragraph"),  # bullet list item
        ("a) lettered list item", "paragraph"),  # lettered list item
        ("ends with a comma,", "paragraph"),  # clause-ending
        ("lowercase start line", "paragraph"),  # headings don't start lowercase
    ],
)
def test_classify_block_rules(block, expected):
    assert dm._classify_block(block) == expected


def test_gap_nodes_preserve_whitespace():
    tree = dm.decompose("One.\n\nTwo.")
    gaps = [g.text for g in tree.iter_kind("gap")]
    assert "\n\n" in gaps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write(tmp_path, text, name="doc.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_cli_decompose_then_compose_is_byte_identical(tmp_path, capsys):
    original = "1.6.9 Systems\n\nThe probe works. Dr. Silva agrees, mostly.\n"
    src = _write(tmp_path, original)

    dm.main([str(src), "--json", "-q"])
    tree_json = capsys.readouterr().out
    tree_file = tmp_path / "tree.json"
    tree_file.write_text(tree_json, encoding="utf-8")

    dm.main(["--compose", str(tree_file)])
    assert capsys.readouterr().out == original


def test_cli_reports_lossless_round_trip(tmp_path, capsys):
    src = _write(tmp_path, "A heading\n\nA body sentence here.")
    dm.main([str(src), "--outline"])
    assert "lossless round-trip OK" in capsys.readouterr().err


def test_cli_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit):
        dm.main([str(tmp_path / "nope.txt")])
