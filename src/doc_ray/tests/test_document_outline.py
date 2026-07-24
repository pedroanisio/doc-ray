"""Document outline — the map that aims the ranged reader (BL-146, P1 item 5).

``read_document_sentences(from_ordinal=…, to_ordinal=…)`` is only as good as the
agent's ability to guess an ordinal. The outline supplies the ordinals.

The substrate is uneven and this suite encodes that honestly. Every extractor
normalizes detected headings to ATX markers in the stored text (PDF via relative
font size in :mod:`doc_ray.domain.pdf_layout`, EPUB/DOCX/MD via markup), but the
PDF heuristic finds plenty in a technical book and almost nothing in a novel —
measured in the live corpus: 723 headings in a Packt title, 17 in *Dune*, **0**
in *Dune Messiah*. An outline that returned ``[]`` for a novel would fail exactly
the document the feature exists for, so a heading-less document falls back to an
even split that is *labelled as such* and never presented as a table of contents.
"""

from __future__ import annotations

from doc_ray.domain.document_outline import (
    build_heading_outline,
    parse_heading_lines,
    plan_uniform_buckets,
    select_headings,
)

# --------------------------------------------------------------------------
# 1. Heading detection over the stored text.
# --------------------------------------------------------------------------


def test_parses_atx_headings_with_level_title_and_absolute_offset():
    text = "# Book One\nthe spice must flow\n### Chapter 3\nmore text\n"
    assert parse_heading_lines(text) == [
        (1, "Book One", 0),
        (3, "Chapter 3", len("# Book One\nthe spice must flow\n")),
    ]


def test_the_offset_is_the_hash_position_so_it_maps_onto_stored_char_spans():
    # Sentence/span offsets are absolute into full_text; the outline must speak
    # the same coordinate system or it cannot be joined to ordinals at all.
    text = "intro line\n## Section\n"
    (entry,) = parse_heading_lines(text)
    assert text[entry[2] : entry[2] + 2] == "##"


def test_a_hash_without_a_space_is_not_a_heading():
    assert parse_heading_lines("#hashtag not a heading\n") == []


def test_seven_hashes_exceeds_the_atx_range_and_is_not_a_heading():
    assert parse_heading_lines("####### too deep\n") == []


def test_an_empty_heading_is_rejected_rather_than_yielding_a_blank_title():
    assert parse_heading_lines("##   \n#\n") == []


def test_heading_titles_are_stripped_of_trailing_whitespace_and_carriage_returns():
    (entry,) = parse_heading_lines("## Chapter 1   \r\n")
    assert entry[1] == "Chapter 1"


def test_a_document_with_no_headings_yields_nothing_rather_than_guessing():
    assert parse_heading_lines("Paul went to Arrakis.\nThe spice flowed.\n") == []


# --------------------------------------------------------------------------
# 2. Turning headings into contiguous, gapless ordinal ranges.
# --------------------------------------------------------------------------


def test_each_heading_runs_until_the_next_one_starts():
    entries = build_heading_outline(
        [(1, "Book One", 0, 0, 1), (2, "Chapter 2", 900, 40, 3)], total_sentences=100
    )
    assert [(e.ordinal_start, e.ordinal_end) for e in entries] == [(0, 39), (40, 99)]
    assert [e.level for e in entries] == [1, 2]
    assert [e.title for e in entries] == ["Book One", "Chapter 2"]


def test_the_last_entry_reaches_the_final_sentence_so_the_map_covers_everything():
    (entry,) = build_heading_outline([(1, "Only", 0, 0, None)], total_sentences=4200)
    assert entry.ordinal_end == 4199


def test_page_numbers_ride_along_so_the_outline_feeds_the_page_reader_too():
    entries = build_heading_outline([(1, "A", 0, 0, 1), (1, "B", 500, 60, 12)], total_sentences=200)
    assert (entries[0].page_start, entries[1].page_start) == (1, 12)


def test_headings_landing_on_the_same_sentence_collapse_to_one_entry():
    # Real case: a PDF title page yields "# CHAPTERHOUSE: # DUNE ### BOOK SIX" —
    # several markers inside a single extracted sentence. Emitting three entries
    # with identical ranges would be noise; the outermost level wins.
    entries = build_heading_outline(
        [(1, "CHAPTERHOUSE", 0, 0, 1), (1, "DUNE", 14, 0, 1), (3, "BOOK SIX", 20, 0, 1)],
        total_sentences=500,
    )
    assert len(entries) == 1
    assert entries[0].ordinal_start == 0 and entries[0].ordinal_end == 499


def test_entries_never_overlap_or_leave_a_gap():
    entries = build_heading_outline(
        [(1, "A", 0, 0, None), (2, "B", 10, 25, None), (2, "C", 20, 80, None)],
        total_sentences=300,
    )
    for previous, following in zip(entries, entries[1:], strict=False):
        assert following.ordinal_start == previous.ordinal_end + 1
    assert entries[0].ordinal_start == 0
    assert entries[-1].ordinal_end == 299


def test_no_headings_yields_no_entries_so_the_caller_can_fall_back():
    assert build_heading_outline([], total_sentences=100) == []


def test_an_empty_document_yields_no_entries():
    assert build_heading_outline([(1, "A", 0, 0, None)], total_sentences=0) == []


# --------------------------------------------------------------------------
# 3. Choosing which headings to keep when a document has more than fit.
# --------------------------------------------------------------------------


def _headings(spec: list[tuple[int, int]]) -> list[tuple[int, str, int]]:
    """Build ``(level, title, char_start)`` headings from ``(level, count)`` pairs."""
    out, offset = [], 0
    for level, count in spec:
        for index in range(count):
            offset += 100
            out.append((level, f"L{level}-{index}", offset))
    return sorted(out, key=lambda h: h[2])


def test_everything_is_kept_when_it_already_fits():
    headings = _headings([(1, 4)])
    assert select_headings(headings, 10) == (headings, 0)


def test_deeper_levels_are_dropped_before_the_map_is_truncated():
    # Keeping the first N in document order would map the preface and call it the
    # book; dropping a level keeps the map proportional to the whole document.
    # Level 1 already fills most of the budget here, so it is the right map.
    headings = _headings([(1, 8), (2, 40)])
    kept, dropped = select_headings(headings, 10)
    assert {h[0] for h in kept} == {1}
    assert dropped == 40


def test_a_level_that_collapses_the_document_is_not_used():
    # Measured on the live corpus: "Princess of Dune" has ONE level-1 heading (its
    # title) and 69 level-2 chapters. Falling back to level 1 answers with a
    # single entry spanning the whole book — an outline that maps nothing. The
    # deeper level must be sampled instead.
    headings = _headings([(1, 1), (2, 69)])
    kept, dropped = select_headings(headings, 6)
    assert len(kept) == 6
    assert dropped == 64
    # Real chapter titles, spread across the book — not one entry spanning it.
    assert sum(1 for h in kept if h[0] == 2) >= 5


def test_a_sampled_level_still_spans_the_whole_document():
    # Sampling must be even, not the first N — otherwise the tail is unreachable.
    headings = _headings([(1, 1), (2, 69)])
    kept, _ = select_headings(headings, 6)
    offsets = [h[2] for h in kept]
    assert offsets == sorted(offsets)
    assert offsets[0] == headings[0][2]  # starts at the top of the document
    # Evenly spread, so the map reaches deep into the book rather than clustering
    # at the front. (The final entry still runs to the last sentence, so the tail
    # is always readable — see build_heading_outline.)
    assert offsets[-1] >= headings[-1][2] * 0.75
    gaps = [b - a for a, b in zip(offsets, offsets[1:], strict=False)]
    assert max(gaps) - min(gaps) <= 200  # ~one heading's spacing


def test_sampling_never_returns_duplicates():
    kept, _ = select_headings(_headings([(1, 1), (2, 100)]), 7)
    assert len({h[2] for h in kept}) == len(kept) == 7


def test_no_headings_selects_nothing():
    assert select_headings([], 10) == ([], 0)


# --------------------------------------------------------------------------
# 4. The fallback grid — for the novels where heading detection finds nothing.
# --------------------------------------------------------------------------


def test_uniform_buckets_cover_every_sentence_exactly_once():
    buckets = plan_uniform_buckets(total_sentences=1000, max_entries=8)
    assert buckets[0][0] == 0
    assert buckets[-1][1] == 999
    for previous, following in zip(buckets, buckets[1:], strict=False):
        assert following[0] == previous[1] + 1


def test_uniform_buckets_respect_the_entry_cap():
    assert len(plan_uniform_buckets(total_sentences=18128, max_entries=20)) == 20


def test_bucket_sizes_differ_by_at_most_one_sentence():
    sizes = [end - start + 1 for start, end in plan_uniform_buckets(1000, 7)]
    assert max(sizes) - min(sizes) <= 1


def test_a_short_document_yields_one_bucket_per_sentence_not_empty_ones():
    assert plan_uniform_buckets(total_sentences=3, max_entries=20) == [(0, 0), (1, 1), (2, 2)]


def test_an_empty_document_yields_no_buckets():
    assert plan_uniform_buckets(total_sentences=0, max_entries=10) == []


def test_a_single_sentence_document_yields_a_single_bucket():
    assert plan_uniform_buckets(total_sentences=1, max_entries=10) == [(0, 0)]
