#!/usr/bin/env python3
"""Lossless hierarchical decomposition of a document.

Decomposes text into a tree:

    document → section → heading | paragraph → sentence → phrase → word

and composes it back **losslessly** — ``compose(decompose(t)) == t`` for any
string ``t``. Losslessness is structural: every character of the source belongs
to exactly one leaf node (words, punctuation, and whitespace are all kept), and
every internal node's text is exactly the concatenation of its children. So
rebuilding is just a depth-first join.

    python docmodel.py report.pdf --outline      # show the structure + counts
    python docmodel.py report.pdf --json > tree.json
    python docmodel.py --compose tree.json        # reconstruct the original text

Note: "lossless" is with respect to the extracted *text*. For .txt that is the
whole file; for .pdf/.docx/.epub it is the text the reader extracts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from extract_phrases import (
    _ABBR_RE,
    _DECIMAL_RE,
    _INITIAL_RE,
    _SENT_BOUNDARY_RE,
    _SHIELD,
    extract_text,
)

# Order of the hierarchy, coarse to fine.
LEVELS = ["document", "section", "heading", "paragraph", "sentence", "phrase", "word"]


# ===========================================================================
# Node model
# ===========================================================================


@dataclass
class Node:
    """A node in the decomposition tree.

    Leaves (``word``, ``punct``, ``space``, ``gap``) carry ``text``; internal
    nodes carry ``children`` and their text is the concatenation of those
    children. ``compose`` reconstructs the exact source substring.
    """

    kind: str
    text: str = ""
    children: list[Node] = field(default_factory=list)

    def compose(self) -> str:
        if not self.children:
            return self.text
        return "".join(child.compose() for child in self.children)

    def iter_kind(self, kind: str):
        """Yield every descendant node of a given kind (depth-first)."""
        if self.kind == kind:
            yield self
        for child in self.children:
            yield from child.iter_kind(kind)

    def to_dict(self) -> dict:
        if self.children:
            return {"kind": self.kind, "children": [c.to_dict() for c in self.children]}
        return {"kind": self.kind, "text": self.text}

    @staticmethod
    def from_dict(d: dict) -> Node:
        children = [Node.from_dict(c) for c in d.get("children", [])]
        node = Node(d["kind"], d.get("text", ""), children)
        if children:  # keep the text invariant for inspection
            node.text = node.compose()
        return node


def compose(node: Node) -> str:
    """Reconstruct the source text from a tree (inverse of :func:`decompose`)."""
    return node.compose()


# ===========================================================================
# Splitting helpers — every helper preserves concatenation (lossless)
# ===========================================================================

# A "gap" leaf holds the separators (whitespace / clause punctuation) that glue
# semantic units together. Keeping them as nodes is what makes compose exact.

_PARA_BREAK_RE = re.compile(r"\n[ \t\r]*\n\s*")  # blank line(s) between blocks
_CLAUSE_RE = re.compile(r"[;,:]\s*")  # clause boundary within a sentence
_TOKEN_RE = re.compile(r"\w+(?:['’\-]\w+)*|\s+|[^\w\s]", re.UNICODE)


def _shield(text: str) -> str:
    """Replace abbreviation / initial / decimal periods with a same-length mark.

    Same length means offsets are preserved, so boundaries found in the shielded
    string map straight back onto the original text.
    """
    text = _ABBR_RE.sub(lambda m: m.group(0).replace(".", _SHIELD), text)
    text = _INITIAL_RE.sub(lambda m: m.group(0).replace(".", _SHIELD), text)
    text = _DECIMAL_RE.sub(lambda m: m.group(1) + _SHIELD + m.group(2), text)
    return text


def _split_keep(text: str, regex: re.Pattern, search_text: str | None = None):
    """Split *text* on *regex*, yielding ``(is_glue, piece)`` and losing nothing.

    Matches are the glue between units; the spans between matches are content.
    ``search_text`` (if given) is searched instead of *text* but slicing uses the
    original *text* — used to search a shielded copy while cutting the real text.
    """
    hay = search_text if search_text is not None else text
    pieces = []
    last = 0
    for m in regex.finditer(hay):
        if m.start() > last:
            pieces.append((False, text[last : m.start()]))
        pieces.append((True, text[m.start() : m.end()]))
        last = m.end()
    if last < len(text):
        pieces.append((False, text[last:]))
    return pieces


# ===========================================================================
# Decomposition (top-down)
# ===========================================================================


def decompose(text: str) -> Node:
    """Decompose document text into the lossless hierarchy."""
    return Node("document", text, _build_sections(text))


_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
# A list item: a bullet followed by text, or a roman/arabic/alpha enumerator
# followed by a separator — e.g. "• item", "XXX – ", "1) ", "a. ". Not headings.
# (A dotted-decimal section number like "1.6.9 Systems" is NOT matched, since its
# separator is followed by a digit, not whitespace — so it stays a heading via
# the short-length rule.)
_LIST_ITEM_RE = re.compile(
    r"^(?:[-*•·]\s+\S"  # bullet:     "• item"
    r"|(?:[IVXLCDM]+|\d+|[A-Za-z])\s*[–—)\.\-]\s)"  # enumerator: "1. ", "XXX – ", "a) "
)


def _classify_block(block_text: str) -> str:
    """Heuristically label a block as a heading or a paragraph.

    Classification is cosmetic — losslessness holds either way — so the rules
    stay simple but conservative: only call something a heading when it looks
    like one and does *not* look like prose or a list item.
    """
    stripped = block_text.strip()
    if not stripped or "\n" in stripped:
        return "paragraph"  # multi-line blocks are body text
    if _MARKDOWN_HEADING_RE.match(stripped):
        return "heading"  # explicit "#" marker is authoritative
    if re.search(r"[.!?;:,]$", stripped):
        return "paragraph"  # ends like a sentence or clause
    if stripped[:1].islower():
        return "paragraph"  # headings don't start lowercase
    if _LIST_ITEM_RE.match(stripped):
        return "paragraph"  # enumerated / bulleted list item
    if len(stripped.split()) <= 12:
        return "heading"  # short, punctuation-free, titley
    return "paragraph"


def _build_sections(text: str) -> list[Node]:
    # Split into blocks + blank-line glue, keeping every character.
    items = []
    for is_glue, piece in _split_keep(text, _PARA_BREAK_RE):
        if piece == "":
            continue
        items.append(("glue" if is_glue else _classify_block(piece), piece))

    # Group blocks into sections; a heading starts a new section.
    def has_content(group):
        return any(kind != "glue" for kind, _ in group)

    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for kind, piece in items:
        if kind == "heading" and has_content(current):
            groups.append(current)
            current = []
        current.append((kind, piece))
    if current:
        groups.append(current)

    sections = []
    for group in groups:
        children = []
        for kind, piece in group:
            if kind == "glue":
                children.append(Node("gap", piece))
            else:
                children.append(_build_block(kind, piece))
        sections.append(Node("section", "".join(c.compose() for c in children), children))
    return sections


def _build_block(kind: str, block_text: str) -> Node:
    shielded = _shield(block_text)
    children = []
    for is_glue, piece in _split_keep(block_text, _SENT_BOUNDARY_RE, shielded):
        if piece == "":
            continue
        children.append(Node("gap", piece) if is_glue else _build_sentence(piece))
    return Node(kind, block_text, children)


def _build_sentence(sentence_text: str) -> Node:
    children = []
    for is_glue, piece in _split_keep(sentence_text, _CLAUSE_RE):
        if piece == "":
            continue
        children.append(Node("gap", piece) if is_glue else _build_phrase(piece))
    return Node("sentence", sentence_text, children)


def _build_phrase(phrase_text: str) -> Node:
    return Node("phrase", phrase_text, _tokenize(phrase_text))


def _tokenize(text: str) -> list[Node]:
    leaves = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.isspace():
            kind = "space"
        elif re.search(r"\w", tok):
            kind = "word"
        else:
            kind = "punct"
        leaves.append(Node(kind, tok))
    return leaves


# ===========================================================================
# Reporting
# ===========================================================================


def counts(node: Node) -> dict[str, int]:
    """Count nodes by kind across the whole tree."""
    tally: dict[str, int] = {}
    stack = [node]
    while stack:
        n = stack.pop()
        tally[n.kind] = tally.get(n.kind, 0) + 1
        stack.extend(n.children)
    return tally


def outline(node: Node, indent: int = 0, max_text: int = 50) -> str:
    """Render a readable indented outline of the tree."""
    lines = []
    _outline_into(node, indent, max_text, lines)
    return "\n".join(lines)


def _outline_into(node: Node, indent: int, max_text: int, lines: list[str]) -> None:
    pad = "  " * indent
    if node.children:
        lines.append(f"{pad}{node.kind} ({len(node.children)})")
        # Recurse into structural children; collapse leaf tokens to one summary.
        if all(c.kind in ("word", "punct", "space", "gap") for c in node.children):
            preview = "".join(c.text for c in node.children).strip()
            if len(preview) > max_text:
                preview = preview[: max_text - 1] + "…"
            lines.append(f"{pad}  “{preview}”")
        else:
            for child in node.children:
                _outline_into(child, indent + 1, max_text, lines)
    else:
        shown = node.text if node.kind != "gap" else repr(node.text)
        shown = shown.replace("\n", "\\n")
        if len(shown) > max_text:
            shown = shown[: max_text - 1] + "…"
        lines.append(f"{pad}{node.kind}: {shown}")


# ===========================================================================
# CLI
# ===========================================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Losslessly decompose a document into its hierarchy, or "
        "compose one back from a saved tree."
    )
    parser.add_argument(
        "file", type=Path, help="Document to decompose, or a .json tree with --compose"
    )
    parser.add_argument(
        "--compose",
        action="store_true",
        help="Treat FILE as a JSON tree and print the reconstructed text",
    )
    parser.add_argument("--json", action="store_true", help="Print the decomposition tree as JSON")
    parser.add_argument(
        "--outline", action="store_true", help="Print a readable indented outline of the tree"
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Do not print the summary / round-trip check to stderr",
    )
    args = parser.parse_args(argv)

    if not args.file.exists():
        sys.exit(f"File not found: {args.file}")

    if args.compose:
        tree = Node.from_dict(json.loads(args.file.read_text(encoding="utf-8")))
        sys.stdout.write(compose(tree))
        return 0

    text = extract_text(args.file)
    tree = decompose(text)

    # Losslessness is a guarantee, not a hope — verify it every run.
    rebuilt = compose(tree)
    if rebuilt != text:
        sys.exit("LOSSLESS CHECK FAILED: compose(decompose(text)) != text")

    if args.json:
        print(json.dumps(tree.to_dict(), ensure_ascii=False))
    elif args.outline:
        print(outline(tree))
    else:
        # Default: emit the tree as JSON (the useful machine-readable form).
        print(json.dumps(tree.to_dict(), ensure_ascii=False))

    if not args.quiet:
        tally = counts(tree)
        order = [k for k in LEVELS if k in tally] + [
            k for k in ("phrase", "word", "punct", "space", "gap") if k in tally and k not in LEVELS
        ]
        summary = ", ".join(
            f"{tally[k]} {k}" + ("" if tally[k] == 1 or k.endswith("s") else "s")
            for k in dict.fromkeys(order)
        )
        print(f"lossless round-trip OK — {summary}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
