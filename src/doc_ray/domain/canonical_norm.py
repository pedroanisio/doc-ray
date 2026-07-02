"""Normalization for the remissive index: a surface form -> its lookup key.

``form_norm`` collapses surface variants — case, Unicode composition, and script — so
that ``Zelenskyy``, ``ZELENSKYY``, ``Zélénskyy``, and a transliterated Cyrillic
spelling all share one key. It reuses the lexeme lemma normalization (NFC + casefold)
and adds ASCII transliteration so cross-script forms of one entity converge onto the
same ``form_index`` row.
"""

from __future__ import annotations

from unidecode import unidecode

from doc_ray.domain.lexemes import norm_lemma


def form_norm(surface: str) -> str:
    """Return the normalized lookup key for a surface form.

    Applies NFC + casefold (via :func:`doc_ray.domain.lexemes.norm_lemma`), then ASCII
    transliteration (Cyrillic/accents -> Latin), a final casefold, and whitespace
    collapse — so case, Unicode-composition, and script differences fold together.

    Args:
        surface: The surface string as seen in text.

    Returns:
        A stable, script- and case-insensitive lookup key.
    """
    base = norm_lemma(surface)
    return " ".join(unidecode(base).casefold().split())
