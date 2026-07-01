"""Lexeme identity, offset tokenization, rule analyzer, and language detection.

Dependency-free (standard library only). Ports the identity core of the
project's lexeme.py but is oriented at the stored `full_text`: `iter_words`
yields word spans as offsets into that exact text, so occurrences map straight
back into document_content. The DB is the persistent registry; the collision-
grow logic lives in db.get_or_create_lexeme (it needs the uid uniqueness check).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterator, Sequence

# Short single-letter tag per POS, used in the human-visible uid.
_POS_TAG = {
    "VERB": "v",
    "NOUN": "n",
    "PROPN": "p",
    "ADJ": "a",
    "ADV": "r",
    "PRON": "o",
    "DET": "d",
    "ADP": "s",
    "NUM": "m",
    "AUX": "x",
    "X": "x",
}


def pos_tag(pos: str) -> str:
    """Return the single-letter tag for a part-of-speech name.

    Args:
        pos: A universal POS name (e.g. ``"VERB"``); case-insensitive.

    Returns:
        The short tag used in human-visible uids, or ``"x"`` if unknown.
    """
    return _POS_TAG.get(pos.upper(), "x")


def norm_lemma(lemma: str) -> str:
    """Normalize a lemma to its canonical stored form (NFC + casefold)."""
    return unicodedata.normalize("NFC", lemma).strip().casefold()


def canonical_key(lemma: str, pos: str, lang: str) -> str:
    """Stable identity string, e.g. 'en:VERB:run'."""
    return f"{lang.strip().lower()}:{pos.strip().upper()}:{norm_lemma(lemma)}"


def _hash(key: str) -> str:
    return hashlib.blake2b(key.encode("utf-8"), digest_size=16).hexdigest()


def uid_candidate(key: str, pos: str, lang: str, size: int = 8) -> str:
    """A candidate uid alias: lx_<lang>.<postag>_<hash[:size]>."""
    return f"lx_{lang.strip().lower()}.{pos_tag(pos)}_{_hash(key)[:size]}"


HASH_HEX_LEN = 32  # blake2b digest_size=16 -> 32 hex chars (max uid hash width)


# --- offset tokenizer ------------------------------------------------------
# A word is a run of letters (no digits, no underscore) with internal
# apostrophes/hyphens: don't, well-being. Offsets are into the given text.
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)


def iter_words(text: str) -> Iterator[tuple[str, int, int]]:
    """Yield ``(surface, start, end)`` for each word, offsets into ``text``.

    Args:
        text: The text to tokenize.

    Yields:
        A ``(surface, start, end)`` tuple per matched word, where ``start`` and
        ``end`` are character offsets into ``text``.
    """
    for m in _WORD_RE.finditer(text):
        yield m.group(0), m.start(), m.end()


# --- rule analyzer (dependency-free English-approx) ------------------------
class RuleAnalyzer:
    """Light de-inflection of the safe cases; POS unknown ('X').

    Cannot identify verbs/nouns — that needs a real tagger (spaCy). Kept so the
    pipeline runs with zero dependencies.
    """

    name = "rule/en-approx"
    version = "0.1"

    def analyze(self, surfaces: list[str]) -> list[tuple[str, str]]:
        """Lemmatize each surface form.

        Args:
            surfaces: The word surfaces to analyze.

        Returns:
            A ``(lemma, pos)`` tuple per surface, in the same order.
        """
        return [self.lemma(s) for s in surfaces]

    def lemma(self, surface: str) -> tuple[str, str]:
        """De-inflect a single surface form.

        Args:
            surface: The word surface to lemmatize.

        Returns:
            A ``(lemma, pos)`` tuple; ``pos`` is always ``"X"`` (unknown).
        """
        w = surface.casefold()
        if re.fullmatch(r"[a-z]+", w):
            if w.endswith("ies") and len(w) > 4:
                return w[:-3] + "y", "X"
            if re.search(r"(s|x|z|ch|sh)es$", w):
                return w[:-2], "X"
            if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
                return w[:-1], "X"
        return w, "X"


# --- language detection (stopword frequency) -------------------------------
_STOPWORDS = {
    "en": (
        "the of and to in a is that for it with as was on are be at by this "
        "have from or an they which you were her all there been has would "
        "their what will we when who more if out so about into than them"
    ),
    "fr": (
        "le la les de des du un une et à en que qui dans pour est il elle sur "
        "ne pas ce se au aux plus par avec son sa ses nous vous ils leur ou "
        "mais comme tout être avoir cette ces ont été aussi"
    ),
    "pt": (
        "de a o que e do da em um para com não uma os no se na por mais as dos "
        "como mas ao ele das seu sua ou quando muito nos já eu também só pelo "
        "pela até isso ela entre era ser está são"
    ),
}
_STOPSETS = {lang: frozenset(words.split()) for lang, words in _STOPWORDS.items()}
_DETECT_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect_language(
    text: str,
    langs: Sequence[str] = ("en", "fr", "pt"),
    min_hits: int = 3,
    margin: int = 2,
    default: str = "en",
) -> str:
    """Detect the dominant language by stopword frequency.

    Args:
        text: The text to inspect.
        langs: Candidate language codes to score, restricted to those with a
            known stopword set.
        min_hits: Minimum stopword hits the winner must reach; below this the
            result is inconclusive.
        margin: Minimum lead the winner must have over the runner-up.
        default: Language code returned when detection is weak or inconclusive.

    Returns:
        The winning language code, or ``default`` if the signal is too weak.
    """
    words = _DETECT_RE.findall(text.casefold())
    if not words:
        return default
    scores = {lang: sum(w in _STOPSETS[lang] for w in words) for lang in langs if lang in _STOPSETS}
    if not scores:
        return default
    best = max(scores, key=lambda lang: scores[lang])
    runner_up = max((s for lang, s in scores.items() if lang != best), default=0)
    if scores[best] < min_hits or scores[best] - runner_up < margin:
        return default
    return best
