"""Tolerant characterization of extract_headings over the REAL corpus.

Every assertion here is deliberately loose: the real PDFs in static/fixtures/
are large and shifting, so we assert structural guarantees (no crash, valid
outline, non-empty texts, strictly-increasing ordinals) plus a handful of
per-file tolerant expectations. The whole module SKIPS cleanly when the corpus
is absent (via the session-scoped `real_corpus` fixture).

These tests are meaningful, not tautological: they open every PDF, actually
run extraction, and would FAIL if extract_headings raised, emitted an invalid
outline, produced empty/blank heading text, repeated an ordinal, or if the
named documents lost their expected source classification / heading yield.
"""

from __future__ import annotations

import fitz

import headings
from invariants import assert_valid_outline


def _source_for(doc: fitz.Document) -> str:
    """Reproduce exactly how headings.extract_headings decides toc vs heuristic."""
    toc = doc.get_toc(simple=True)
    if toc and len({e[2] for e in toc}) > 1:
        return "toc"
    return "heuristic"


def _ordinals_strictly_increasing(hs) -> bool:
    ords = [h.ordinal for h in hs]
    return all(a < b for a, b in zip(ords, ords[1:]))


def test_every_corpus_pdf_extracts_cleanly(real_corpus, open_doc):
    """For EVERY pdf: open, extract without raising, valid outline, non-empty
    texts, strictly-increasing ordinals. Iterates the session-scoped fixture
    directly (subtests-style loop, with the failing filename surfaced)."""
    assert real_corpus, "real_corpus fixture yielded no files"

    checked = 0
    for name, path in real_corpus.items():
        doc = open_doc(str(path))
        try:
            source = _source_for(doc)

            # Must not raise on any real document.
            hs = headings.extract_headings(doc)

            # Structural invariants. TOC fast-path trusts the author's outline,
            # so monotonic nesting is only required on the heuristic path.
            assert_valid_outline(
                hs,
                page_count=doc.page_count,
                require_monotonic=(source != "toc"),
            )

            # Every heading carries real, non-blank text.
            assert all(h.text and h.text.strip() for h in hs), (
                f"{name}: found empty/blank heading text"
            )

            # Ordinals are strictly increasing across the whole list.
            assert _ordinals_strictly_increasing(hs), (
                f"{name}: ordinals not strictly increasing: "
                f"{[h.ordinal for h in hs]}"
            )
        finally:
            doc.close()
        checked += 1

    # Guard against a silently-empty loop turning this into a no-op pass.
    assert checked == len(real_corpus)


def test_airfrance_is_toc_sourced_and_large(real_corpus, open_doc):
    """AirFrance447_BEA.pdf ships a rich embedded outline (>50 entries)."""
    name = "AirFrance447_BEA.pdf"
    if name in real_corpus:
        doc = open_doc(str(real_corpus[name]))
        try:
            assert _source_for(doc) == "toc", f"{name} expected toc source"
            hs = headings.extract_headings(doc)
            assert len(hs) > 50, f"{name} expected >50 headings, got {len(hs)}"
            # A toc-sourced outline is emitted verbatim with source signal.
            assert all(h.signals.get("source") == "toc" for h in hs)
        finally:
            doc.close()


def test_pals_law_is_toc_sourced(real_corpus, open_doc):
    """PALS_LAW-v1.5.4.pdf has an embedded outline of at least 10 entries."""
    name = "PALS_LAW-v1.5.4.pdf"
    if name in real_corpus:
        doc = open_doc(str(real_corpus[name]))
        try:
            assert _source_for(doc) == "toc", f"{name} expected toc source"
            hs = headings.extract_headings(doc)
            assert len(hs) >= 10, f"{name} expected >=10 headings, got {len(hs)}"
        finally:
            doc.close()


def test_metodologia_is_heuristic_with_numbered_headings(real_corpus, open_doc):
    """metodologia-instrumentacao-agentes.pdf has NO usable embedded outline,
    so it runs the heuristic path and yields several dot-numbered headings."""
    name = "metodologia-instrumentacao-agentes.pdf"
    if name in real_corpus:
        doc = open_doc(str(real_corpus[name]))
        try:
            assert _source_for(doc) == "heuristic", f"{name} expected heuristic source"
            hs = headings.extract_headings(doc)
            numbered = [h for h in hs if headings.RE_DOTNUM.match(h.text)]
            assert len(numbered) >= 5, (
                f"{name} expected >=5 RE_DOTNUM headings, got {len(numbered)}: "
                f"{[h.text for h in numbered]}"
            )
        finally:
            doc.close()
