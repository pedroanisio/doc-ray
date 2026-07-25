"""Pure verdict logic for the term-frequency pre-flight (MCP Item 10 / #16).

Given raw candidate search terms and the corpus-wide use counts the adapter reads
from the ``lexeme_stats`` rollup, this module decides — with no database access —
a categorical ``flood_risk`` verdict per term and the steering note that goes with
it. The adapter supplies the numbers; the domain decides, mirroring the
``document_outline`` / ``entity_profiles`` split (BL-146): pure logic here, testable
with no database.

**FR-5 finding (recorded in docs/mcp-service.md).** The frequency this reports is
the *lexeme-lemma* frequency, keyed on :func:`doc_ray.domain.lexemes.norm_lemma` —
the exact normalization the lexeme writer applies before storing a lemma
(``persistence.get_or_create_lexeme``), so the number describes the term the
lexeme layer actually indexed. It is a strong proxy for, but deliberately not
identical to, what the two search tools match:

* ``search_sentences`` matches a **case-insensitive literal substring** (ILIKE),
  no stemming or lemmatization;
* ``full_text_search`` matches **Snowball-stemmed** tokens (``plainto_tsquery``
  with the document's text-search config).

So this verdict is a corpus-*saturation* signal, not a per-tool hit predictor, and
the payload carries the raw numbers so a caller can disagree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from doc_ray.domain.lexemes import norm_lemma

#: Batch cap: this triages an agent's candidate search terms, it is not a bulk
#: frequency dump. Bounds the ``= ANY`` array so the single statement stays cheap.
MAX_TERMS = 50

#: ``flood_risk`` cut points on a term's *share* of the scope's total lexeme uses.
#: Chosen to flag corpus-**saturating** terms — the ones a substring/stem search
#: returns as a wall of clustered hits, starving coverage of the rest — derived
#: from the Zipfian structure of natural-language lexis: the function words plus a
#: handful of top content lemmas clear ``HIGH_SHARE``; common content words land in
#: the moderate band; scene-specific vocabulary sits well below it. They are
#: parameters, not law — the payload exposes ``uses``/``total_uses``/``share`` so a
#: caller can recompute, and ``docs/mcp-service.md`` records how to re-tune them
#: against a live corpus's measured distribution.
HIGH_SHARE = 0.005  # >= 0.5% of all lexeme uses in the scope
MODERATE_SHARE = 0.0005  # >= 0.05%

LOW = "low"
MODERATE = "moderate"
HIGH = "high"


class InvalidTerms(ValueError):
    """Raised when a batch of terms cannot be served (empty, all-blank, or oversized)."""


@dataclass(frozen=True)
class PreparedTerm:
    """A cleaned term ready for lookup: the echoed spelling and the lookup key."""

    term: str  # first raw spelling that produced this key, trimmed (echoed back)
    key: str  # norm_lemma(term) — the exact lexeme lookup key


@dataclass(frozen=True)
class TermVerdict:
    """One term's frequency verdict with the numbers that justify it."""

    term: str
    normalized_term: str
    uses: int
    total_uses: int
    share: float
    flood_risk: str
    attested: bool
    note: str | None


def prepare_terms(raw: Sequence[str]) -> list[PreparedTerm]:
    """Normalize, drop blanks, de-duplicate by key, and bound the batch.

    Preserves first-seen order; the first raw spelling of a key is the one echoed
    back. Raises :class:`InvalidTerms` on an oversized batch or one that is empty
    after cleaning — conditions the caller must fix, not ones the server should
    paper over by guessing.

    Args:
        raw: The caller's candidate terms, exactly as supplied.

    Returns:
        One :class:`PreparedTerm` per distinct searchable term, in input order.
    """
    if len(raw) > MAX_TERMS:
        raise InvalidTerms(f"at most {MAX_TERMS} terms per call, got {len(raw)}")
    out: list[PreparedTerm] = []
    seen: set[str] = set()
    for item in raw:
        key = norm_lemma(item)
        if not key or key in seen:  # blank/punctuation-only, or already queued
            continue
        seen.add(key)
        out.append(PreparedTerm(term=item.strip(), key=key))
    if not out:
        raise InvalidTerms("no searchable terms after normalization (all blank?)")
    return out


def flood_risk(uses: int, total_uses: int) -> str:
    """Return the categorical verdict from a term's share of the scope's total uses.

    ``uses <= 0`` (unattested) and ``total_uses <= 0`` (empty scope) both yield
    ``"low"`` and never divide by zero.
    """
    if uses <= 0 or total_uses <= 0:
        return LOW
    share = uses / total_uses
    if share >= HIGH_SHARE:
        return HIGH
    if share >= MODERATE_SHARE:
        return MODERATE
    return LOW


def _note(term: str, uses: int, risk: str) -> str | None:
    """The BL-145-voice steering line for a verdict, or ``None`` when nothing to steer."""
    if uses <= 0:
        return (
            f"{term!r} is unattested in this corpus — a search for it will most "
            "likely return nothing. Check the spelling, or try a related term."
        )
    if risk == HIGH:
        return (
            f"{term!r} saturates the corpus: a keyword search will return a wall of "
            "hits clustered in one region and starve coverage of the rest. Prefer a "
            "rarer, scene-specific term, or read a section evenly with "
            "read_document_outline + read_document_sentences."
        )
    if risk == MODERATE:
        return (
            f"{term!r} is common here — a search may hit its per-call limit. Pair it "
            "with a rarer term, or scope the read to a section, to keep coverage even."
        )
    return None


def build_verdicts(
    prepared: Sequence[PreparedTerm],
    counts: Mapping[str, int],
    total_uses: int,
) -> list[TermVerdict]:
    """Assemble one :class:`TermVerdict` per prepared term from the adapter's counts.

    Args:
        prepared: The terms from :func:`prepare_terms`, order preserved.
        counts: Mapping of normalized key -> corpus-wide use count (missing = 0).
        total_uses: The scope's total lexeme uses (the share denominator).

    Returns:
        One verdict per input term, in the same order.
    """
    total = int(total_uses)
    verdicts: list[TermVerdict] = []
    for item in prepared:
        uses = int(counts.get(item.key, 0))
        risk = flood_risk(uses, total)
        share = (uses / total) if (uses > 0 and total > 0) else 0.0
        verdicts.append(
            TermVerdict(
                term=item.term,
                normalized_term=item.key,
                uses=uses,
                total_uses=total,
                share=share,
                flood_risk=risk,
                attested=uses > 0,
                note=_note(item.term, uses, risk),
            )
        )
    return verdicts
