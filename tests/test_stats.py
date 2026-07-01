"""Tests for the statistics summary (``format_stats``)."""

from __future__ import annotations

import re

import extract_phrases as ep


def test_empty_returns_placeholder():
    assert ep.format_stats([]) == "No sentences extracted."


def test_counts_total_unique_and_duplicates():
    stats = ep.format_stats(["A cat sat.", "A cat sat.", "A dog ran."])
    assert re.search(r"Sentences \(total\) +: 3", stats)
    assert re.search(r"Sentences \(unique\) +: 2", stats)
    assert re.search(r"Duplicates +: 1", stats)


def test_longest_and_shortest_by_character_length():
    stats = ep.format_stats(["Hi.", "A much longer sentence here."])
    assert '"A much longer sentence here."' in stats
    assert '"Hi."' in stats


def test_paragraph_lines_present_only_when_given():
    with_paras = ep.format_stats(["One here.", "Two here."], n_paragraphs=2)
    assert re.search(r"Paragraphs +: 2", with_paras)
    assert "Sentences/paragraph" in with_paras

    without = ep.format_stats(["One here.", "Two here."])
    assert "Paragraphs" not in without


def test_top_words_are_listed():
    stats = ep.format_stats(["the cat and the dog", "the cat again"])
    assert "Top words" in stats
    assert "the" in stats
