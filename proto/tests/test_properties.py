"""Property-style invariant sweep over many deterministic synthetic PDFs.

The universal structural guarantees of ``extract_headings`` (see
``invariants.assert_valid_outline``) must hold for ALL inputs, not just the
canonical fixtures. This module *generates* a family of ~15+ documents that
differ along independent axes — number of numbered headings, presence/size of
surrounding body text, page count, and heading font size — and asserts, for
every one of them, that:

  * ``assert_valid_outline(hs, page_count=doc.page_count,
    require_monotonic=True)`` holds (levels 1..4, non-empty text, confidence in
    range, ordinals strictly increasing, pages in [0, page_count), monotonic
    nesting),
  * every emitted level lies in 1..4,
  * ordinals are strictly increasing AND form the contiguous sequence
    0,1,...,len-1 that ``extract_headings`` assigns.

Nothing here is randomised: every document is a pure function of its loop
index, so the sweep is fully reproducible.

Three specific structural cases are pinned as their own tests:
  * a body-only document (no headings) -> ``[]``, which vacuously satisfies the
    invariants,
  * deep dot-numbering ``1 / 1.1 / 1.1.1 / 1.1.1.1`` -> levels ``[1,2,3,4]``,
  * numbering that would imply level 5 (``1.1.1.1.1``) -> the extra depth is
    capped at 4.

To guard against a vacuous ("always-green") harness, ``test_checker_rejects_*``
prove that ``assert_valid_outline`` and the local level/ordinal checks actually
FAIL on deliberately malformed outlines.
"""

from __future__ import annotations

import dataclasses

import pytest

import headings
from invariants import assert_valid_outline, levels, texts
from synthpdf import L, PdfSpec, body_block, build_pdf, heading, open_doc


# Distinct heading words so that, on multi-page documents, genuine headings do
# NOT collapse to a single running-head signature (strip_running_heads folds
# digits to '#', so "1 Alpha"/"2 Alpha" would share a signature and be dropped
# once >=3 of them appear on a >=3-page doc). Using different words keeps each
# heading a bona-fide, surviving heading.
_HEAD_WORDS = (
    "Introduction", "Methods", "Results", "Discussion", "Conclusion",
    "Background", "Analysis", "Findings", "Summary", "Overview",
)


def _write(tmp_path, name, lines):
    """Render `lines` to a fresh PDF under tmp_path and return its path str."""
    return build_pdf(str(tmp_path / name), PdfSpec(lines=list(lines)))


def _assert_outline_properties(hs, doc):
    """The invariants that must hold for the outline of ANY document."""
    # Universal structural invariants (levels 1..4, text non-empty, confidence
    # in range, ordinals strictly increasing, pages in range, monotonic nest).
    assert_valid_outline(hs, page_count=doc.page_count, require_monotonic=True)

    # Redundant-but-explicit per the task: every level within 1..4.
    for h in hs:
        assert isinstance(h.level, int)
        assert 1 <= h.level <= 4, f"level {h.level} out of 1..4 for {h.text!r}"

    # Ordinals strictly increasing; extract_headings assigns them densely from
    # 0, so they must be exactly 0,1,...,len-1.
    ordinals = [h.ordinal for h in hs]
    assert ordinals == sorted(ordinals), f"ordinals not sorted: {ordinals}"
    assert len(set(ordinals)) == len(ordinals), f"ordinals not unique: {ordinals}"
    assert ordinals == list(range(len(hs))), f"ordinals not dense 0..n-1: {ordinals}"


# --------------------------------------------------------------------------- #
# Deterministic document family.                                              #
#                                                                             #
# Each spec is a pure function of the loop index `i`, varying four axes:      #
#   - n_head:  count of numbered headings           (1..4)                    #
#   - body_n:  body_block lines around each heading (0..3, i.e. presence+size)#
#   - hsize:   heading font size                    (14 / 16 / 18)            #
#   - multipage: whether each heading opens a new page (drives page_count)    #
# This yields the required >=15 documents spanning single- and multi-page,    #
# body-rich and body-free, small- and large-heading layouts.                 #
# --------------------------------------------------------------------------- #

def _make_case(i):
    """Return (label, lines) for sweep document `i`."""
    n_head = 1 + (i % 4)
    body_n = i % 4                       # 0 => a heading-only / body-free stretch
    hsize = 14.0 + (i % 3) * 2.0         # 14 / 16 / 18
    multipage = (i % 2 == 1)             # alternate single-/multi-page docs

    lines = list(body_block(1 + (i % 2)))   # a little leading body (>=1 line)
    for j in range(n_head):
        word = _HEAD_WORDS[(i + j) % len(_HEAD_WORDS)]
        h = heading(f"{j + 1} {word}", size=hsize)
        if multipage:
            h = dataclasses.replace(h, new_page=True)
        lines.append(h)
        if body_n:
            lines += body_block(body_n, start=i * 13 + j * 3)

    label = (f"i={i} n_head={n_head} body_n={body_n} "
             f"hsize={hsize} multipage={multipage}")
    return label, lines


_CASES = [_make_case(i) for i in range(15)]
_IDS = [c[0] for c in _CASES]


@pytest.mark.parametrize("label,lines", _CASES, ids=_IDS)
def test_invariants_hold_for_generated_doc(label, lines, tmp_path):
    """PROPERTY: for every generated doc, the outline invariants hold."""
    path = _write(tmp_path, "sweep.pdf", lines)
    doc = open_doc(path)
    hs = headings.extract_headings(doc)

    _assert_outline_properties(hs, doc)

    # Sanity floor: these docs each contain genuine numbered headings with
    # DISTINCT words, so at least one heading must be recovered (this makes the
    # sweep non-vacuous — a totally-empty result would be a real regression).
    assert len(hs) >= 1, f"expected >=1 heading for {label}, got {texts(hs)}"
    # Every recovered heading is one we planted (a numbered, distinct-word line).
    planted = {w.lower() for w in _HEAD_WORDS}
    for h in hs:
        tail = h.text.split(maxsplit=1)[-1].lower()
        assert tail in planted, f"unexpected heading {h.text!r} in {label}"


# --------------------------------------------------------------------------- #
# Required specific structural documents.                                     #
# --------------------------------------------------------------------------- #

def test_body_only_doc_yields_empty_and_is_valid(tmp_path):
    """A doc with only body text -> no headings, and [] satisfies invariants."""
    lines = body_block(20)
    doc = open_doc(_write(tmp_path, "bodyonly.pdf", lines))
    hs = headings.extract_headings(doc)

    assert hs == [], f"body-only doc should yield no headings, got {texts(hs)}"
    # The empty outline must still pass every invariant (vacuously true).
    _assert_outline_properties(hs, doc)


def test_deep_numbering_reaches_level_four(tmp_path):
    """1 / 1.1 / 1.1.1 / 1.1.1.1 -> dot-depth drives levels 1,2,3,4."""
    lines = (
        body_block(3)
        + [heading("1 Alpha")] + body_block(3)
        + [heading("1.1 Beta")] + body_block(3)
        + [heading("1.1.1 Gamma")] + body_block(3)
        + [heading("1.1.1.1 Delta")] + body_block(3)
    )
    doc = open_doc(_write(tmp_path, "deep.pdf", lines))
    hs = headings.extract_headings(doc)

    assert texts(hs) == ["1 Alpha", "1.1 Beta", "1.1.1 Gamma", "1.1.1.1 Delta"]
    assert levels(hs) == [1, 2, 3, 4]
    _assert_outline_properties(hs, doc)


def test_level_five_numbering_is_capped_at_four(tmp_path):
    """1.1.1.1.1 would imply depth 5; extract_headings caps the level at 4."""
    lines = (
        body_block(3)
        + [heading("1 Alpha")] + body_block(3)
        + [heading("1.1 Beta")] + body_block(3)
        + [heading("1.1.1 Gamma")] + body_block(3)
        + [heading("1.1.1.1 Delta")] + body_block(3)
        + [heading("1.1.1.1.1 Epsilon")] + body_block(3)
    )
    doc = open_doc(_write(tmp_path, "level5.pdf", lines))
    hs = headings.extract_headings(doc)

    assert texts(hs) == [
        "1 Alpha", "1.1 Beta", "1.1.1 Gamma", "1.1.1.1 Delta", "1.1.1.1.1 Epsilon",
    ]
    # Depth-5 numbering is clamped to 4 (both by min(depth,4) and _repair_tree),
    # so the sequence is 1,2,3,4,4 — never a 5.
    assert levels(hs) == [1, 2, 3, 4, 4]
    assert max(levels(hs)) == 4
    _assert_outline_properties(hs, doc)


# --------------------------------------------------------------------------- #
# Anti-tautology guards: prove the property checks actually reject bad data.  #
# If these did NOT raise, the sweep above would be worthless.                 #
# --------------------------------------------------------------------------- #

def _fake(level=1, text="X", page=0, ordinal=0, confidence=1.0):
    return headings.Heading(level=level, text=text, page=page,
                            ordinal=ordinal, confidence=confidence, signals={})


def test_checker_rejects_level_five():
    """assert_valid_outline must reject a level outside 1..4."""
    with pytest.raises(AssertionError):
        assert_valid_outline([_fake(level=5)], page_count=1)


def test_checker_rejects_nonincreasing_ordinals():
    """assert_valid_outline must reject non-increasing ordinals."""
    bad = [_fake(ordinal=0), _fake(ordinal=0)]  # duplicate -> not strictly up
    with pytest.raises(AssertionError):
        assert_valid_outline(bad, page_count=1)


def test_checker_rejects_out_of_range_page():
    """assert_valid_outline must reject a page >= page_count."""
    with pytest.raises(AssertionError):
        assert_valid_outline([_fake(page=5)], page_count=1)


def test_checker_rejects_level_jump():
    """With require_monotonic, a jump of +2 in nesting must be rejected."""
    bad = [_fake(level=1, ordinal=0), _fake(level=3, ordinal=1)]
    with pytest.raises(AssertionError):
        assert_valid_outline(bad, page_count=1, require_monotonic=True)


class _FakeDoc:
    """Minimal stand-in exposing only .page_count for the local property helper."""
    page_count = 1


def test_local_property_helper_rejects_sparse_ordinals():
    """_assert_outline_properties must reject ordinals that are not 0..n-1
    (e.g. [0, 2]) even though assert_valid_outline alone would accept them."""
    # [0, 2] is strictly increasing (passes assert_valid_outline) but NOT dense.
    sparse = [_fake(ordinal=0), _fake(ordinal=2, page=0)]
    assert_valid_outline(sparse, page_count=1)          # the weaker check passes
    with pytest.raises(AssertionError):                 # the local check does not
        _assert_outline_properties(sparse, _FakeDoc())
