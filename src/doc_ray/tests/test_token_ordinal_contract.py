"""Characterization guard for the persisted spaCy token ordinal contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doc_ray.adapters import annotation_spacy
from doc_ray.adapters.annotation_spacy import SpacyAnnotator

REPO_ROOT = Path(__file__).parents[3]


@dataclass(frozen=True)
class _Morph:
    values: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str]:
        return self.values


@dataclass(frozen=True)
class _Token:
    text: str
    i: int
    idx: int
    is_alpha: bool
    lemma_: str
    pos_: str
    tag_: str
    morph: _Morph = field(default_factory=_Morph)


def test_build_tokens_persists_document_global_spacy_ordinals(monkeypatch: Any) -> None:
    """Ordinals cross sentence boundaries; punctuation stays and whitespace does not."""
    doc = [
        _Token("Alpha", 0, 0, True, "alpha", "PROPN", "NNP"),
        _Token(".", 1, 5, False, ".", "PUNCT", "."),
        _Token(" ", 2, 6, False, " ", "SPACE", "_SP"),
        _Token("Beta", 3, 7, True, "beta", "PROPN", "NNP"),
        _Token(".", 4, 11, False, ".", "PUNCT", "."),
    ]
    span_map = {
        (0, 5): "span-alpha",
        (5, 6): "span-period-1",
        (7, 11): "span-beta",
        (11, 12): "span-period-2",
    }
    monkeypatch.setattr(
        annotation_spacy.db,
        "get_or_create_lexeme",
        lambda _conn, _cache, lemma, pos, lang: (f"lex-{lang}-{pos}-{lemma}", True),
    )

    rows, _features, token_ids = SpacyAnnotator()._build_tokens(
        object(),
        doc,
        span_map,
        lambda offset: "sentence-1" if offset < 7 else "sentence-2",
        "en",
    )

    first_sentence_ordinals = [row[3] for row in rows if row[2] == "sentence-1"]
    second_sentence_ordinals = [row[3] for row in rows if row[2] == "sentence-2"]
    assert max(first_sentence_ordinals) < min(second_sentence_ordinals)
    assert [row[3] for row in rows] == [0, 1, 3, 4]
    assert [(row[3], row[4]) for row in rows if row[5] == "PUNCT"] == [
        (1, None),
        (4, None),
    ]
    assert 2 not in token_ids


def test_nlp_architecture_documents_the_token_ordinal_contract() -> None:
    spec = (REPO_ROOT / "docs" / "nlp_architecture.md").read_text(encoding="utf-8")
    prose = " ".join(spec.split())
    folded_prose = prose.casefold()

    assert "document-global spaCy token index (`t.i`)" in prose
    assert "ordinals continue across sentence boundaries" in prose
    assert "whitespace-only tokens are skipped" in folded_prose
    assert "punctuation tokens are persisted with `lexeme_uid` null" in folded_prose
    assert "token index within sentence" not in folded_prose


def test_nlp_architecture_documents_term_frequency_verdict_tiers() -> None:
    architecture = (REPO_ROOT / "docs" / "nlp_architecture.md").read_text(encoding="utf-8")
    retrieval_section = architecture.split("## 7. Search and retrieval paths", maxsplit=1)[1]

    for contract_term in (
        'measurement: "exact"',
        "component_upper_bound",
        "saturation ceiling",
        "sentence_scan",
        "SENTENCE_HIGH_HITS",
        "match_clause",
        "`capped`",
    ):
        assert contract_term in retrieval_section
