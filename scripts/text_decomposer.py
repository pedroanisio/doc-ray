#!/usr/bin/env python3
"""
text_decomposer.py - Decompose text into a hierarchy:

    paragraphs -> sentences -> phrases -> words

This file is self-contained (it bundles the sentence segmenter). Every level
has a dependency-free path so it runs anywhere, plus an optional higher-accuracy
path for the two hard levels:

    sentences : rule-based by default; NLTK Punkt if requested/available.
    phrases   : GRAMMATICAL chunking (noun/verb/prepositional phrases) via NLTK
                if available; otherwise a punctuation/clause splitter.

Usage:
    python text_decomposer.py --demo
    python text_decomposer.py file.txt
    echo "The tall building stood under the old bridge." | python text_decomposer.py
    python text_decomposer.py file.txt --json
    python text_decomposer.py file.txt --phrase-engine rule --aggressive
"""

from __future__ import annotations
import argparse
import json
import re
import statistics
import sys
from collections import Counter

# ===========================================================================
# LEVEL 1 - PARAGRAPHS
# ===========================================================================

def split_paragraphs(text: str) -> list[str]:
    """Split on blank lines. Single newlines are treated as soft wraps."""
    paras = re.split(r"\n\s*\n", text.strip())
    return [re.sub(r"\s+", " ", p).strip() for p in paras if p.strip()]


# ===========================================================================
# LEVEL 2 - SENTENCES  (bundled from the sentence extractor)
# ===========================================================================

ABBREVIATIONS = {
    "mr", "mrs", "ms", "mx", "dr", "prof", "sr", "jr", "st", "gen", "rep",
    "sen", "gov", "pres", "supt", "det", "rev", "fr", "hon", "lt", "col",
    "capt", "cpl", "sgt", "maj", "cmdr", "adm", "brig", "etc", "vs", "viz",
    "cf", "al", "eg", "ie", "ca", "approx", "esp", "am", "pm", "jan", "feb",
    "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "inc", "ltd", "co", "corp", "llc", "plc", "dept", "univ", "assn", "bros",
    "mfg", "est", "vol", "pp", "pg", "fig", "ed", "repr",
}

_STASH = "\uE000"
_CLOSERS = r'"\'\u201d\u2019\)\]\u00bb'
_OPENERS = r'"\'\u201c\u2018\(\[\u00ab'

_SENT_BOUNDARY = re.compile(
    rf'(?<=[.!?])(?P<tail>[{_CLOSERS}]*)\s+'
    rf'(?=[{_OPENERS}]*[A-Z0-9\u00c0-\u024f])'
)


def _protect(text: str):
    stash: dict[str, str] = {}

    def keep(m: re.Match) -> str:
        token = f"{_STASH}{len(stash)}{_STASH}"
        stash[token] = m.group(0)
        return token

    text = re.sub(r'https?://\S+|www\.\S+', keep, text)
    text = re.sub(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b', keep, text)
    text = re.sub(r'\b\d+(?:\.\d+)+\b', keep, text)
    text = re.sub(r'\.\.\.+|\u2026', keep, text)
    text = re.sub(r'\b(?:[A-Za-z]\.){2,}', keep, text)
    text = re.sub(r'\b[A-Z]\.(?=\s|$)', keep, text)
    if ABBREVIATIONS:
        abbr = '|'.join(sorted(ABBREVIATIONS, key=len, reverse=True))
        text = re.sub(rf'\b(?:{abbr})\.', keep, text, flags=re.IGNORECASE)
    return text, stash


def _restore(text: str, stash: dict[str, str]) -> str:
    for token, original in stash.items():
        text = text.replace(token, original)
    return text


def split_sentences_rule(text: str) -> list[str]:
    out: list[str] = []
    protected, stash = _protect(text)
    last = 0
    for m in _SENT_BOUNDARY.finditer(protected):
        cut = m.start() + len(m.group("tail"))
        piece = _restore(protected[last:cut], stash).strip()
        if piece:
            out.append(piece)
        last = m.end()
    tail = _restore(protected[last:], stash).strip()
    if tail:
        out.append(tail)
    return out


def split_sentences_nltk(text: str) -> list[str]:
    import nltk
    _ensure_nltk("tokenizers/punkt", "punkt")
    _ensure_nltk("tokenizers/punkt_tab", "punkt_tab")
    return [s.strip() for s in nltk.sent_tokenize(text) if s.strip()]


def split_sentences(text: str, engine: str = "rule") -> list[str]:
    if engine == "rule":
        return split_sentences_rule(text)
    if engine == "nltk":
        return split_sentences_nltk(text)
    if engine == "auto":
        try:
            return split_sentences_nltk(text)
        except Exception:
            return split_sentences_rule(text)
    raise ValueError(f"Unknown sentence engine: {engine!r}")


# ===========================================================================
# LEVEL 3 - PHRASES
# ===========================================================================

# --- Grammatical chunk grammar (used by the NLTK engine) -------------------
# Edit these patterns to change what counts as a phrase.
_CHUNK_GRAMMAR = r"""
  NP: {<DT|PRP\$>?<CD>*<JJ.*|VBN|VBG>*<NN.*|NNS>+}   # determiner + adjectives + noun(s)
      {<PRP>}                                         # a pronoun on its own
  VP: {<MD>?<RB.*>*<VB.*>+<RB.*>*}                    # (modal) + verb(s) + adverbs
  PP: {<IN|TO><NP>}                                   # preposition + noun phrase
"""

# --- Rule-based clause splitter (dependency-free fallback) ------------------
_PHRASE_DELIM = re.compile(r'\s*[,;:\u2014\u2013]\s+|\s+[-\u2013\u2014]\s+')
_CONJUNCTIONS = re.compile(
    r'\s+(?=(?:and|but|or|nor|yet|so|because|although|though|while|whereas|'
    r'since|unless|until|if|that|which|who|when|where)\b)',
    re.IGNORECASE,
)


def split_phrases_rule(sentence: str, aggressive: bool = False) -> list[dict]:
    """Split a sentence into segments at punctuation (and optionally
    before conjunctions). Segments are labelled None - these are clause-level
    approximations, not true grammatical constituents."""
    parts = _PHRASE_DELIM.split(sentence)
    if aggressive:
        expanded = []
        for p in parts:
            expanded.extend(_CONJUNCTIONS.split(p))
        parts = expanded
    return [{"label": None, "text": p.strip()} for p in parts if p.strip()]


def split_phrases_nltk(sentence: str) -> list[dict]:
    """Extract grammatical phrases (NP / VP / PP) via POS tagging + chunking.
    Returns top-level chunks so phrases do not overlap; loose tokens that
    fall outside any chunk are emitted with label None so nothing is lost."""
    import nltk
    from nltk import pos_tag, word_tokenize, RegexpParser
    _ensure_nltk("tokenizers/punkt", "punkt")
    _ensure_nltk("tokenizers/punkt_tab", "punkt_tab")
    _ensure_nltk("taggers/averaged_perceptron_tagger", "averaged_perceptron_tagger")
    _ensure_nltk("taggers/averaged_perceptron_tagger_eng", "averaged_perceptron_tagger_eng")

    tree = RegexpParser(_CHUNK_GRAMMAR).parse(pos_tag(word_tokenize(sentence)))

    phrases: list[dict] = []
    loose: list[str] = []

    def flush_loose():
        if loose:
            text = " ".join(loose).strip()
            if any(ch.isalnum() for ch in text):
                phrases.append({"label": None, "text": text})
            loose.clear()

    for node in tree:
        if hasattr(node, "label"):          # a chunk subtree
            flush_loose()
            words = [w for w, _ in node.leaves()]
            phrases.append({"label": node.label(), "text": " ".join(words)})
        else:                               # a bare (word, tag) token
            word, _ = node
            loose.append(word)
    flush_loose()
    return phrases


def split_phrases(sentence: str, engine: str = "auto",
                  aggressive: bool = False) -> list[dict]:
    if engine == "grammatical":
        return split_phrases_nltk(sentence)
    if engine == "rule":
        return split_phrases_rule(sentence, aggressive=aggressive)
    if engine == "auto":
        try:
            return split_phrases_nltk(sentence)
        except Exception:
            return split_phrases_rule(sentence, aggressive=aggressive)
    raise ValueError(f"Unknown phrase engine: {engine!r}")


# ===========================================================================
# LEVEL 4 - WORDS
# ===========================================================================

# Keeps internal apostrophes, hyphens, and dots: don't, well-being, 3.14, a.m.
_WORD = re.compile(
    r"[A-Za-z0-9\u00c0-\u024f]+(?:[.'\u2019\-][A-Za-z0-9\u00c0-\u024f]+)*"
)


def split_words(text: str, keep_punct: bool = False) -> list[str]:
    if keep_punct:
        return re.findall(r"[A-Za-z0-9\u00c0-\u024f'\u2019\-]+|[^\w\s]", text)
    return _WORD.findall(text)


# ===========================================================================
# ORCHESTRATION
# ===========================================================================

def decompose(text: str, *, phrase_engine: str = "auto",
              sentence_engine: str = "rule", aggressive: bool = False,
              keep_punct: bool = False) -> dict:
    """Return the full paragraphs -> sentences -> phrases -> words tree."""
    used_phrase_engine = phrase_engine
    if phrase_engine == "auto":
        try:                                   # probe once so we can report it
            split_phrases_nltk("Probe sentence here.")
            used_phrase_engine = "grammatical"
        except Exception:
            used_phrase_engine = "rule"

    paragraphs = []
    for p_idx, para in enumerate(split_paragraphs(text), 1):
        sentences = []
        for s_idx, sent in enumerate(split_sentences(para, sentence_engine), 1):
            sentences.append({
                "index": s_idx,
                "text": sent,
                "phrases": split_phrases(sent, used_phrase_engine, aggressive),
                "words": split_words(sent, keep_punct),
            })
        paragraphs.append({"index": p_idx, "text": para, "sentences": sentences})

    result = {"phrase_engine": used_phrase_engine, "paragraphs": paragraphs}
    result["stats"] = compute_stats(result)
    return result


# ===========================================================================
# STATS
# ===========================================================================

def compute_stats(result: dict) -> dict:
    """Aggregate counts and ratios over the decomposition tree."""
    sent_word_counts: list[int] = []
    phrase_count = 0
    label_counts: dict[str, int] = {}
    word_freq: Counter[str] = Counter()
    total_word_chars = 0

    for para in result["paragraphs"]:
        for s in para["sentences"]:
            words = s["words"]
            sent_word_counts.append(len(words))
            for w in words:
                total_word_chars += len(w)
                word_freq[w.lower()] += 1
            phrase_count += len(s["phrases"])
            for ph in s["phrases"]:
                label = ph["label"] or "(unlabeled)"
                label_counts[label] = label_counts.get(label, 0) + 1

    n_paragraphs = len(result["paragraphs"])
    n_sentences = len(sent_word_counts)
    total_words = sum(sent_word_counts)
    unique_words = len(word_freq)

    def _ratio(num: float, den: float, ndigits: int = 2) -> float:
        return round(num / den, ndigits) if den else 0.0

    return {
        "paragraphs": n_paragraphs,
        "sentences": n_sentences,
        "phrases": phrase_count,
        "words": total_words,
        "unique_words": unique_words,
        "sentence_length": {
            "mean": _ratio(total_words, n_sentences),
            "median": statistics.median(sent_word_counts) if sent_word_counts else 0,
            "min": min(sent_word_counts) if sent_word_counts else 0,
            "max": max(sent_word_counts) if sent_word_counts else 0,
        },
        "ratios": {
            "sentences_per_paragraph": _ratio(n_sentences, n_paragraphs),
            "phrases_per_sentence": _ratio(phrase_count, n_sentences),
            "words_per_sentence": _ratio(total_words, n_sentences),
            "chars_per_word": _ratio(total_word_chars, total_words),
            "lexical_diversity": _ratio(unique_words, total_words, 3),
        },
        "phrase_labels": label_counts,
        "top_words": word_freq.most_common(10),
    }


# ===========================================================================
# HELPERS / OUTPUT
# ===========================================================================

def _ensure_nltk(find_path: str, download_name: str) -> None:
    import nltk
    try:
        nltk.data.find(find_path)
    except LookupError:
        try:
            nltk.download(download_name, quiet=True)
        except Exception:
            pass


def render_tree(result: dict) -> str:
    lines = [f"[phrase engine: {result['phrase_engine']}]", ""]
    for para in result["paragraphs"]:
        lines.append(f"Paragraph {para['index']}")
        for s in para["sentences"]:
            lines.append(f"  Sentence {s['index']}: {s['text']}")
            lines.append("    Phrases:")
            for ph in s["phrases"]:
                tag = f"[{ph['label']}] " if ph["label"] else "[--] "
                lines.append(f"      {tag}{ph['text']}")
            lines.append("    Words: " + " | ".join(s["words"]))
        lines.append("")
    return "\n".join(lines).rstrip()


def render_stats(stats: dict) -> str:
    def row(label: str, value, width: int = 24) -> str:
        dots = "." * max(3, width - len(label))
        return f"  {label} {dots} {value}"

    lines = ["", "=" * 46, "STATS", "=" * 46, "Structure"]
    lines.append(row("Paragraphs", stats["paragraphs"]))
    lines.append(row("Sentences", stats["sentences"]))
    lines.append(row("Phrases", stats["phrases"]))
    lines.append(row("Words (tokens)", stats["words"]))
    lines.append(row("Unique words (types)", stats["unique_words"]))

    sl = stats["sentence_length"]
    lines += ["", "Sentence length (words)"]
    lines.append(row("Mean", sl["mean"]))
    lines.append(row("Median", sl["median"]))
    lines.append(row("Min / Max", f"{sl['min']} / {sl['max']}"))

    r = stats["ratios"]
    lines += ["", "Ratios"]
    lines.append(row("Sentences / paragraph", r["sentences_per_paragraph"]))
    lines.append(row("Phrases / sentence", r["phrases_per_sentence"]))
    lines.append(row("Words / sentence", r["words_per_sentence"]))
    lines.append(row("Chars / word", r["chars_per_word"]))
    lines.append(row("Lexical diversity", r["lexical_diversity"]))

    if stats["phrase_labels"]:
        lines += ["", "Phrase labels"]
        for label, count in sorted(stats["phrase_labels"].items(),
                                   key=lambda kv: (-kv[1], kv[0])):
            lines.append(row(label, count))

    if stats["top_words"]:
        lines += ["", "Top words"]
        for i, (word, count) in enumerate(stats["top_words"], 1):
            lines.append(f"  {i:2}. {word} ({count})")

    return "\n".join(lines)


# ===========================================================================
# CLI
# ===========================================================================

_DEMO_TEXT = (
    "The tall building stood under the old bridge. She quickly ran to the "
    "store and bought fresh bread.\n\n"
    "Dr. Smith paid $3.14 at 9 a.m.; then he left."
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Decompose text: paragraphs -> sentences -> phrases -> words."
    )
    ap.add_argument("path", nargs="?", help="Input file. Omit to read stdin.")
    ap.add_argument("--phrase-engine", choices=["auto", "grammatical", "rule"],
                    default="auto", help="How to find phrases (default: auto).")
    ap.add_argument("--sentence-engine", choices=["rule", "nltk", "auto"],
                    default="rule", help="How to split sentences (default: rule).")
    ap.add_argument("--aggressive", action="store_true",
                    help="Rule phrase engine: also split before conjunctions.")
    ap.add_argument("--keep-punct", action="store_true",
                    help="Emit punctuation as separate word tokens.")
    ap.add_argument("--json", action="store_true", help="Output JSON.")
    ap.add_argument("--demo", action="store_true", help="Run on a built-in sample.")
    args = ap.parse_args(argv)

    if args.demo:
        text = _DEMO_TEXT
    elif args.path:
        with open(args.path, "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    result = decompose(
        text,
        phrase_engine=args.phrase_engine,
        sentence_engine=args.sentence_engine,
        aggressive=args.aggressive,
        keep_punct=args.keep_punct,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_tree(result))
        print(render_stats(result["stats"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
