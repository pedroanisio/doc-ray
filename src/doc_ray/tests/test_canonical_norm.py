"""Tests for the remissive-index lookup key (doc_ray.domain.canonical_norm)."""

from __future__ import annotations

from doc_ray.domain.canonical_norm import form_norm


def test_case_and_accent_fold():
    assert form_norm("Zelenskyy") == "zelenskyy"
    assert form_norm("ZELENSKYY") == "zelenskyy"
    assert form_norm("Zélénskyy") == "zelenskyy"


def test_whitespace_collapsed():
    assert form_norm("  the   President  ") == "the president"


def test_transliteration_is_ascii_lowercase_and_idempotent():
    out = form_norm("Зеленський")  # Cyrillic
    assert out and out.isascii()
    assert out == out.casefold()
    assert form_norm(out) == out  # normalizing a normalized form is a no-op


def test_deterministic():
    assert form_norm("Бахмут") == form_norm("Бахмут")
