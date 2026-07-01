#!/usr/bin/env python3
"""Extract all sentences from a document.

Reads text from many document formats, rejoins lines that were wrapped
mid-sentence (as PDFs and column layouts do), and splits the result into
sentences — one per line.

    python extract_phrases.py report.pdf
    python extract_phrases.py notes.txt -o sentences.txt
    python extract_phrases.py book.epub --unique

Supported formats: .txt .md .text .pdf .docx .epub .html .htm
Optional dependencies (only for the matching format):
    pip install pypdf          # .pdf
    pip install python-docx    # .docx
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path


# ===========================================================================
# 1. Read raw text from a document (one reader per format)
# ===========================================================================

def read_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("PDF support needs 'pypdf'. Install with: pip install pypdf")
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def read_docx(path: Path) -> str:
    try:
        import docx  # python-docx
    except ImportError:
        sys.exit("DOCX support needs 'python-docx'. Install with: pip install python-docx")
    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


class _HTMLTextExtractor(HTMLParser):
    """Collect visible text from HTML, skipping script/style, breaking blocks."""

    _SKIP = {"script", "style", "head", "title"}
    _BREAK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAK:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


def read_html(path: Path) -> str:
    return _html_to_text(read_txt(path))


def read_epub(path: Path) -> str:
    """Read an EPUB (a zip of XHTML) using only the standard library."""
    texts: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in _epub_spine_order(zf):
            try:
                raw = zf.read(name).decode("utf-8", errors="replace")
            except KeyError:
                continue
            texts.append(_html_to_text(raw))
    return "\n".join(texts)


def _epub_spine_order(zf: zipfile.ZipFile) -> list[str]:
    """Content-document paths in reading order; else all (x)html files sorted."""
    import posixpath
    from xml.etree import ElementTree as ET

    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
        opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
        opf = ET.fromstring(zf.read(opf_path))
        base = posixpath.dirname(opf_path)
        ns = {"o": "http://www.idpf.org/2007/opf"}
        manifest = {
            item.get("id"): item.get("href")
            for item in opf.iterfind(".//o:manifest/o:item", ns)
        }
        order = []
        for ref in opf.iterfind(".//o:spine/o:itemref", ns):
            href = manifest.get(ref.get("idref"))
            if href:
                order.append(posixpath.normpath(posixpath.join(base, href)))
        if order:
            return order
    except (KeyError, AttributeError, ET.ParseError):
        pass
    return sorted(
        n for n in zf.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))
    )


READERS = {
    ".txt": read_txt,
    ".md": read_txt,
    ".text": read_txt,
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".epub": read_epub,
    ".html": read_html,
    ".htm": read_html,
}


def extract_text(path: Path) -> str:
    """Read a document's raw text, choosing the reader by file extension."""
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        supported = ", ".join(sorted(READERS))
        sys.exit(f"Unsupported file type '{path.suffix}'. Supported: {supported}")
    return reader(path)


# ===========================================================================
# 2. Reflow wrapped lines, then split into sentences
# ===========================================================================

# Abbreviations whose trailing period must NOT end a sentence.
# ("no"/"No" are deliberately excluded — the word "no" ends sentences far more
# often than "No." abbreviates "number".)
_ABBR = (
    "Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Mt|vs|etc|Vol|Fig|figs|al|Inc|Ltd|Co|"
    "Corp|Cf|cf|approx|Dept|Univ|pp|e\\.g|i\\.e"
)
_ABBR_RE = re.compile(rf"\b(?:{_ABBR})\.", re.IGNORECASE)
_INITIAL_RE = re.compile(r"\b[A-Z]\.")            # single initials: "J." "K."
_DECIMAL_RE = re.compile(r"(\d)\.(\d)")           # decimals: "1.5"
_DEHYPHEN_RE = re.compile(r"(\w)-\s*\n\s*(\w)")   # word split across a line break
_INLINE_NEWLINE_RE = re.compile(r"\s*\n\s*")      # a wrapped-line newline
# Sentence boundary: . ! or ? then whitespace then the start of a new sentence
# (capital letter, digit, or an opening quote/bracket).
_SENT_BOUNDARY_RE = re.compile(r'(?<=[.!?])\s+(?=["“(\[A-Z0-9])')

_SHIELD = "\x00"  # temporary stand-in for a period that must not split


def reflow(text: str) -> str:
    """Rejoin lines wrapped mid-sentence, preserving blank-line paragraph breaks.

    Turns the multi-line layout of PDFs/EPUBs back into whole sentences so the
    splitter sees sentences, not fragments.
    """
    text = text.replace("\xa0", " ").replace("’", "'")
    text = _DEHYPHEN_RE.sub(r"\1\2", text)
    paragraphs = re.split(r"\n[ \t]*\n", text)
    reflowed = (
        re.sub(r"[ \t]+", " ", _INLINE_NEWLINE_RE.sub(" ", p)).strip()
        for p in paragraphs
    )
    return "\n".join(p for p in reflowed if p)


def _split_paragraph(segment: str, min_words: int) -> list[str]:
    """Split a single reflowed paragraph (one line) into its sentences."""
    segment = _ABBR_RE.sub(lambda m: m.group(0).replace(".", _SHIELD), segment)
    segment = _INITIAL_RE.sub(lambda m: m.group(0).replace(".", _SHIELD), segment)
    segment = _DECIMAL_RE.sub(lambda m: m.group(1) + _SHIELD + m.group(2), segment)

    sentences = []
    for part in _SENT_BOUNDARY_RE.split(segment):
        sentence = " ".join(part.replace(_SHIELD, ".").split())
        if sentence and len(sentence.split()) >= min_words:
            sentences.append(sentence)
    return sentences


def split_sentences(text: str, min_words: int = 1) -> list[str]:
    """Split already-reflowed text into a flat list of sentences."""
    sentences = []
    for line in text.split("\n"):
        sentences.extend(_split_paragraph(line, min_words))
    return sentences


def extract_sentences(text: str, min_words: int = 1) -> list[str]:
    """Extract every sentence from raw document text (flat list)."""
    return split_sentences(reflow(text), min_words)


def extract_paragraphs(text: str, min_words: int = 1) -> list[list[str]]:
    """Extract sentences grouped by paragraph.

    Returns one inner list per paragraph, each holding that paragraph's
    sentences. Paragraph boundaries come from :func:`reflow` — blank lines in
    plain text, or block-element breaks in HTML/EPUB. Empty paragraphs are
    dropped.
    """
    groups = []
    for line in reflow(text).split("\n"):
        sentences = _split_paragraph(line, min_words)
        if sentences:
            groups.append(sentences)
    return groups


# ===========================================================================
# 3. Statistics
# ===========================================================================

def format_stats(sentences: list[str], n_paragraphs: int | None = None) -> str:
    """Build a human-readable statistics summary for the extracted sentences."""
    from collections import Counter

    if not sentences:
        return "No sentences extracted."

    word_counts = [len(s.split()) for s in sentences]
    char_counts = [len(s) for s in sentences]
    total_words = sum(word_counts)
    unique = len(set(sentences))
    longest = max(sentences, key=len)
    shortest = min(sentences, key=len)

    # Most common words (letters only, length >= 3, case-insensitive).
    tokens = re.findall(r"[^\W\d_]{3,}", " ".join(sentences).lower(), flags=re.UNICODE)
    top_words = Counter(tokens).most_common(10)

    def trunc(s: str, n: int = 60) -> str:
        return s if len(s) <= n else s[: n - 1] + "…"

    lines = [
        "===== Statistics =====",
        f"Sentences (total)    : {len(sentences):,}",
        f"Sentences (unique)   : {unique:,}",
        f"Duplicates           : {len(sentences) - unique:,}",
    ]
    if n_paragraphs is not None:
        lines.append(f"Paragraphs           : {n_paragraphs:,}")
        if n_paragraphs:
            lines.append(
                f"Sentences/paragraph  : avg {len(sentences) / n_paragraphs:.1f}"
            )
    lines += [
        f"Total words          : {total_words:,}",
        f"Total characters     : {sum(char_counts):,}",
        f"Words per sentence   : avg {total_words / len(sentences):.1f}  "
        f"min {min(word_counts)}  max {max(word_counts)}",
        f"Chars per sentence   : avg {sum(char_counts) / len(sentences):.1f}  "
        f"min {min(char_counts)}  max {max(char_counts)}",
        f'Longest sentence     : "{trunc(longest)}" ({len(longest)} chars)',
        f'Shortest sentence    : "{trunc(shortest)}" ({len(shortest)} chars)',
        "Top words            : "
        + ", ".join(f"{w} ({c})" for w, c in top_words),
        "======================",
    ]
    return "\n".join(lines)


# ===========================================================================
# 4. Command-line interface
# ===========================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract all sentences from a document, one per line."
    )
    parser.add_argument("file", type=Path,
                        help="Document to read (.txt/.md/.pdf/.docx/.epub/.html)")
    parser.add_argument("--min-words", type=int, default=1,
                        help="Skip sentences with fewer than this many words "
                             "(default: 1 = keep everything)")
    parser.add_argument("--unique", action="store_true",
                        help="Drop duplicate sentences, keeping first-seen order")
    parser.add_argument("--paragraphs", action="store_true",
                        help="Group sentences by paragraph: one sentence per line, "
                             "with a blank line between paragraphs")
    parser.add_argument("-o", "--output", type=Path,
                        help="Write sentences to this file instead of stdout")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Do not print the summary count to stderr")
    args = parser.parse_args(argv)

    if not args.file.exists():
        sys.exit(f"File not found: {args.file}")

    text = extract_text(args.file)

    if args.paragraphs:
        groups = extract_paragraphs(text, min_words=args.min_words)
        all_sentences = [s for group in groups for s in group]
        n_paragraphs = len(groups)
        body = "\n\n".join("\n".join(group) for group in groups)
        written = len(all_sentences)
    else:
        all_sentences = extract_sentences(text, min_words=args.min_words)
        n_paragraphs = None
        out = list(dict.fromkeys(all_sentences)) if args.unique else all_sentences
        body = "\n".join(out)
        written = len(out)

    if args.output:
        args.output.write_text(body + "\n" if body else "", encoding="utf-8")
        target = str(args.output)
    else:
        print(body)
        target = "stdout"

    if not args.quiet:
        print(format_stats(all_sentences, n_paragraphs=n_paragraphs), file=sys.stderr)
        print(f"Wrote {written:,} sentences -> {target}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
