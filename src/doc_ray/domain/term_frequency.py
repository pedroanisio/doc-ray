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

* ``search_sentences`` defaults to a **case-insensitive literal substring** and
  offers opt-in ``surface_word_v1`` whole-word phrase verification;
* ``full_text_search`` matches **Snowball-stemmed** tokens (``plainto_tsquery``
  with the document's text-search config).

So this verdict is a corpus-*saturation* signal, not a per-tool hit predictor, and
the payload carries the raw numbers so a caller can disagree.

**BL-164 — what the lexeme index cannot represent.** The lexeme layer tokenizes
with :data:`doc_ray.domain.lexemes._WORD_RE`, which excludes ``_`` and digits from
word characters, so no key containing either can ever exist in the index.
:func:`norm_lemma` does not tokenize, so a key like ``shared_buffers`` used to be
looked up against an index that structurally could not hold it — returning 0 and
reporting ``attested=false`` ("a search will most likely return nothing") for a
term that occurs throughout the corpus. A zero count from an impossible lookup is
not evidence of absence. Such terms are now flagged ``indexable=False`` and
answered ``unknown``: ``uses``/``share``/``attested`` are ``None``, the constituent
parts the index *does* hold are reported as evidence, and the note points at
``search_sentences``, whose literal-substring match handles them correctly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from doc_ray.domain.lexemes import iter_words, norm_lemma
from doc_ray.domain.phrase_profile import PROFILE_SURFACE_WORD_V1, find_occurrences

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
#: The verdict for a term the lexeme index cannot represent (BL-164) — distinct
#: from ``LOW``, which is a measurement, not an inability to measure.
UNKNOWN = "unknown"

#: Provenance of the numeric result. A phrase upper bound is useful steering
#: evidence, but it must never be presented as an exact occurrence count.
EXACT = "exact"
COMPONENT_UPPER_BOUND = "component_upper_bound"
UNAVAILABLE = "unavailable"

#: Provenance of the *verdict* (distinct from ``measurement``, which describes the
#: lexeme-side numbers). ``lexeme_lemma`` is the historical proxy; ``sentence_scan``
#: is a bounded SQL candidate fetch followed by ``surface_word_v1`` verification
#: and occurrence counting (#46).
METHOD_LEXEME = "lexeme_lemma"
METHOD_SENTENCE_SCAN = "sentence_scan"

#: Sentence-scan bands, keyed to the page a search actually serves. A mirror of
#: ``entrypoints.api.context.MAX_PAGE_SIZE`` — the domain must not import an
#: entrypoint, so a contract test keeps the two equal (the ``shaping.py`` pattern).
#: Parameters, not law: ``docs/mcp-service.md`` records how to re-tune them, exactly
#: as it does for ``HIGH_SHARE``/``MODERATE_SHARE``.
SENTENCE_PAGE_SIZE = 200  # one full page of results
#: >10 full pages saturates any single read, so counting further buys nothing —
#: this doubles as the scan's cap, which is what makes the count bounded.
SENTENCE_HIGH_HITS = 2001


class InvalidTerms(ValueError):
    """Raised when a batch of terms cannot be served (empty, all-blank, or oversized)."""


def index_parts(term: str) -> tuple[str, ...]:
    """Return the lexeme keys the annotation layer would actually index for *term*.

    Runs the production tokenizer (:func:`doc_ray.domain.lexemes.iter_words`, the
    one :mod:`doc_ray.adapters.annotation` writes occurrences from) and normalizes
    each word the way the lexeme writer does. ``"shared_buffers"`` yields
    ``("shared", "buffers")``; ``"spice"`` yields ``("spice",)``; punctuation yields
    ``()``.

    Args:
        term: The caller's raw term.

    Returns:
        The normalized lexeme keys the index can hold for this term, in order.
    """
    return tuple(norm_lemma(word) for word, _start, _end in iter_words(term))


@dataclass(frozen=True)
class PartCount:
    """One constituent lexeme of an unindexable term, and its corpus-wide uses."""

    part: str
    uses: int


@dataclass(frozen=True)
class PreparedTerm:
    """A cleaned term ready for lookup: the echoed spelling and the lookup key."""

    term: str  # first raw spelling that produced this key, trimmed (echoed back)
    key: str  # norm_lemma(term) — the exact lexeme lookup key
    #: True when every requested unit can be represented: either one exact
    #: lexeme or 2+ words separated only by whitespace. False for underscore
    #: compounds, alphanumerics, and punctuation, whose counts are unknowable here.
    indexable: bool
    #: The keys the index holds (one for exact terms, 2+ for phrases/evidence).
    parts: tuple[str, ...]
    measurement: str


@dataclass(frozen=True)
class TermVerdict:
    """One term's frequency verdict with the numbers that justify it.

    ``measurement`` distinguishes exact single-lexeme counts, phrase component
    upper bounds, and unavailable compound counts. For a positive phrase upper
    bound, ``attested`` remains ``None`` because components do not prove adjacency.
    """

    term: str
    normalized_term: str
    uses: int | None
    total_uses: int
    share: float | None
    flood_risk: str
    attested: bool | None
    indexable: bool
    measurement: str
    #: Evidence for an unindexable term: the parts it was split into, with their
    #: counts. Empty for indexable terms, where ``uses`` is already the answer.
    parts: tuple[PartCount, ...]
    note: str | None
    #: Which measurement produced ``flood_risk``: the lexeme-lemma proxy, or a
    #: bounded candidate scan plus profile verification (#46).
    method: str = METHOD_LEXEME
    #: Sentence-scan results — ``None`` exactly when the method is not a scan.
    #: Never 0: an unmeasured field that reads as a measurement is the BL-164
    #: defect this whole module exists to prevent.
    sentence_hits: int | None = None
    total_sentences: int | None = None
    capped: bool | None = None
    #: Verified sentence-scan results. ``sentence_hits`` is the bounded candidate
    #: count; these fields report the boundary/adjacency-valid occurrences that
    #: survive the named profile. All are null on the lexeme path.
    profile: str | None = None
    occurrence_sentences: int | None = None
    occurrences: int | None = None


def _is_whitespace_phrase(term: str, parts: Sequence[str]) -> bool:
    """Return whether 2+ indexed words fully cover *term* with whitespace separators."""
    if len(parts) < 2:
        return False
    stripped = term.strip()
    matches = list(iter_words(stripped))
    if matches[0][1] != 0 or matches[-1][2] != len(stripped):
        return False
    return all(
        stripped[left[2] : right[1]].isspace()
        for left, right in zip(matches, matches[1:], strict=False)
    )


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
        parts = index_parts(item)
        # Indexable only if the tokenizer yields exactly one word AND it is the
        # key we would look up. Anything else (underscore compound, alphanumeric,
        # punctuation) cannot exist in the index under `key`, so querying it would
        # buy a guaranteed miss and report it as absence (BL-164).
        exact = len(parts) == 1 and parts[0] == key
        phrase = _is_whitespace_phrase(item, parts)
        indexable = exact or phrase
        measurement = EXACT if exact else COMPONENT_UPPER_BOUND if phrase else UNAVAILABLE
        out.append(
            PreparedTerm(
                term=item.strip(),
                key=key,
                indexable=indexable,
                parts=parts,
                measurement=measurement,
            )
        )
    if not out:
        raise InvalidTerms("no searchable terms after normalization (all blank?)")
    return out


def lookup_keys(prepared: Sequence[PreparedTerm]) -> list[str]:
    """Return the de-duplicated lexeme keys to fetch counts for, in first-seen order.

    An indexable term contributes its own key; an unindexable one contributes the
    parts the index actually holds, so its evidence rides along in the *same*
    single statement rather than costing extra round-trips (D-024). Keys that
    cannot exist in the index are never asked for.

    Args:
        prepared: The terms from :func:`prepare_terms`.

    Returns:
        The distinct keys the read adapter should fetch.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for item in prepared:
        for key in (item.key,) if item.measurement == EXACT else item.parts:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def scan_text(item: PreparedTerm) -> str:
    """Return the raw spelling a candidate scan and verifier should use.

    Candidate generation segments this value and verification applies the phrase
    profile to it, so both want what the caller typed — not ``key``, which is
    casefolded and NFC-normalized for the lexeme index.
    """
    return item.term


def scan_keys(prepared: Sequence[PreparedTerm]) -> list[str]:
    """Return the keys of the terms a sentence scan should serve, in input order.

    Every term the lexeme index cannot measure exactly — whitespace phrases and
    the compounds/alphanumerics of BL-164 alike. The mechanism is identical for
    both, so distinguishing them would be arbitrary (#39 OD-2). Exact single
    lexemes are excluded: they are already measured, and scanning them would
    silently answer a different question (substring, not lemma).
    """
    return [item.key for item in prepared if item.measurement != EXACT]


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


@dataclass(frozen=True)
class SentenceScan:
    """A bounded candidate scan plus its verified phrase-occurrence counts.

    ``capped`` marks a count that stopped at the cap: the true total is unknown
    and all three counts are floors. The optional defaults preserve the pre-#46
    domain seam for callers that supply a legacy scan; persistence-created scans
    always populate the verified fields and profile.
    """

    hits: int
    capped: bool
    occurrence_sentences: int | None = None
    occurrences: int | None = None
    profile: str | None = None

    def __post_init__(self) -> None:
        """Reject internally contradictory verified counts."""
        if self.hits < 0:
            raise ValueError("sentence hits cannot be negative")
        verified = (self.occurrence_sentences, self.occurrences, self.profile)
        if all(value is None for value in verified):
            return
        if any(value is None for value in verified):
            raise ValueError("verified scans require occurrence counts and profile together")
        assert self.occurrence_sentences is not None
        assert self.occurrences is not None
        if not 0 <= self.occurrence_sentences <= self.hits:
            raise ValueError("occurrence sentences must be between zero and candidate hits")
        if self.occurrences < self.occurrence_sentences:
            raise ValueError("occurrences cannot be below occurrence sentences")


def verify_sentence_candidates(term: str, texts: Sequence[str], *, cap: int) -> SentenceScan:
    """Verify bounded SQL candidates under ``surface_word_v1``.

    Candidate generation is deliberately a permissive, index-backed superset.
    This pure second stage enforces word boundaries and adjacency, counts every
    overlapping occurrence, and records how many candidate sentences contain at
    least one. Reaching the candidate cap makes every reported count a floor.
    """
    if cap <= 0:
        raise ValueError("sentence scan cap must be positive")
    per_sentence = [len(find_occurrences(text, term)) for text in texts]
    occurrence_sentences = sum(count > 0 for count in per_sentence)
    return SentenceScan(
        hits=len(texts),
        capped=len(texts) >= cap,
        occurrence_sentences=occurrence_sentences,
        occurrences=sum(per_sentence),
        profile=PROFILE_SURFACE_WORD_V1,
    )


def sentence_flood_risk(hits: int, capped: bool) -> str:
    """Return the categorical verdict from a verified occurrence-sentence count.

    ``low`` when the hits fit in one page (the caller reads them all), ``moderate``
    up to ten pages, ``high`` beyond that — or whenever the count was capped, since
    a floor at the cap is already past the high boundary.
    """
    if capped or hits >= SENTENCE_HIGH_HITS:
        return HIGH
    if hits >= SENTENCE_PAGE_SIZE:
        return MODERATE
    return LOW


def _unindexable_note(term: str, parts: Sequence[PartCount]) -> str:
    """Explain why there is no count, and name the tool that does answer (BL-164).

    Deliberately never says "unattested" or "returns nothing": the whole defect was
    an impossible lookup dressed as measured absence. It states the gap, hands over
    the parts as evidence, and redirects to the tool whose matching rule covers the
    term.
    """
    if parts:
        shown = ", ".join(f"{part.part!r} ({part.uses})" for part in parts)
        indexed_as = f"the lexeme layer indexes it only as separate words — {shown}"
    else:
        indexed_as = "the lexeme layer does not index it at all"
    return (
        f"{term!r} cannot be counted by the lexeme index, which tokenizes on "
        f"underscores and digits, so {indexed_as}. Its frequency is therefore "
        f"unknown — this is NOT an absence, and the term may well be frequent. "
        f"search_sentences matches a case-insensitive literal substring and "
        f"handles this term directly; use it to find out."
    )


def _phrase_note(term: str, parts: Sequence[PartCount], upper_bound: int) -> str:
    """Explain phrase evidence without dressing an upper bound as an exact count."""
    if upper_bound <= 0:
        absent = next(part.part for part in parts if part.uses <= 0)
        return (
            f"{term!r} is unattested as a lexeme phrase because component {absent!r} "
            "has no indexed uses. Confirm retrieval semantics with "
            "full_text_search(mode='phrase')."
        )
    rarest = min(parts, key=lambda part: part.uses)
    return (
        f"{term!r} has a component upper bound of {upper_bound} uses from its "
        f"rarest component {rarest.part!r}. This is NOT an exact phrase count: "
        "positive component counts do not prove adjacency. Confirm matches with "
        "full_text_search(mode='phrase')."
    )


def _scan_note(term: str, scan: SentenceScan, total_sentences: int | None, risk: str) -> str | None:
    """The BL-145-voice steering line for a verified sentence scan.

    States the bound before the number whenever the count is a floor, and stays
    silent in the low band where the numbers already answer the question.
    """
    verified = scan.occurrence_sentences if scan.occurrence_sentences is not None else scan.hits
    profile = scan.profile or PROFILE_SURFACE_WORD_V1
    scope = f" of {total_sentences}" if total_sentences is not None and total_sentences > 0 else ""
    denominator_warning = (
        " The total-sentence denominator is unavailable because its corpus_stats "
        "rollup is empty; occurrenceSentences remains measured, and floodRisk uses "
        "the absolute bounded verified-sentence bands."
        if total_sentences is None
        else ""
    )
    if scan.capped:
        return (
            f"{term!r} reached the bounded candidate cap. There are at least "
            f"{scan.hits} candidate sentences; sentenceHits, occurrenceSentences, "
            f"and occurrences are all floors under {profile}; "
            "unseen candidates may add verified occurrences, so floodRisk is high. "
            f"Prefer a rarer or longer phrase.{denominator_warning}"
        )
    if verified <= 0:
        return (
            f"{term!r} has no boundary-valid occurrence under {profile} in the "
            f"{scan.hits} candidate sentences. Candidate substring matches can be "
            "false positives; this is measured absence of the requested surface "
            "phrase, NOT absence of the idea — check the spelling, or try a "
            f"synonym.{denominator_warning}"
        )
    if risk == HIGH:
        return (
            f"{term!r} saturates the corpus: {verified}{scope} sentences contain a "
            f"verified {profile} occurrence, so a search returns a wall of hits clustered in "
            "one region and starves coverage of the rest. Prefer a rarer or longer "
            "phrase, or read a section evenly with read_document_outline + "
            f"read_document_sentences.{denominator_warning}"
        )
    if risk == MODERATE:
        return (
            f"{term!r} has verified {profile} occurrences in {verified}{scope} "
            "sentences — more than one page, so "
            "a search will hit its per-call limit. Scope the read to one document, "
            f"or pair it with a rarer term, to keep coverage even.{denominator_warning}"
        )
    return denominator_warning.strip() or None


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
    *,
    scans: Mapping[str, SentenceScan] | None = None,
    total_sentences: int = 0,
) -> list[TermVerdict]:
    """Assemble one :class:`TermVerdict` per prepared term from the adapter's counts.

    A term the lexeme index cannot measure exactly is answered by its scan when
    one is supplied, and declines exactly as before when one is not (#39 FR-6):
    an honest decline beats a wrong measurement.

    Args:
        prepared: The terms from :func:`prepare_terms`, order preserved.
        counts: Mapping of normalized key -> corpus-wide use count (missing = 0).
        total_uses: The scope's total lexeme uses (the share denominator).
        scans: Bounded candidates and verified counts keyed by
            :attr:`PreparedTerm.key`, for the terms named by :func:`scan_keys`.
            Omitted keys fall back.
        total_sentences: The scope's total sentences, reported alongside a scan.

    Returns:
        One verdict per input term, in the same order.
    """
    total = int(total_uses)
    scanned = dict(scans or {})
    verdicts: list[TermVerdict] = []
    for item in prepared:
        scan = scanned.get(item.key) if item.measurement != EXACT else None
        if scan is not None:
            # Verified under the same profile as phrase search. The lexeme-side
            # component counts ride along as secondary evidence, not the answer.
            parts = tuple(PartCount(part, int(counts.get(part, 0))) for part in item.parts)
            component_bound = (
                min((part.uses for part in parts), default=0)
                if item.measurement == COMPONENT_UPPER_BOUND
                else None
            )
            verified_sentences = (
                scan.occurrence_sentences if scan.occurrence_sentences is not None else scan.hits
            )
            risk = sentence_flood_risk(verified_sentences, scan.capped)
            # Any fetched candidate proves the visible corpus is non-empty. A
            # zero denominator is then stale even if profile verification rejects
            # that candidate; expose it as null + an actionable note (#42).
            reported_total_sentences = (
                None if scan.hits > 0 and int(total_sentences) <= 0 else int(total_sentences)
            )
            verdicts.append(
                TermVerdict(
                    term=item.term,
                    normalized_term=item.key,
                    uses=component_bound,
                    total_uses=total,
                    share=(
                        (component_bound / total)
                        if (component_bound and total > 0)
                        else (0.0 if component_bound is not None else None)
                    ),
                    flood_risk=risk,
                    # Verification proves positives. An uncapped zero proves
                    # absence; a capped zero is incomplete and stays unknown.
                    attested=(True if verified_sentences > 0 else (None if scan.capped else False)),
                    indexable=item.indexable,
                    measurement=item.measurement,
                    parts=parts,
                    note=_scan_note(item.term, scan, reported_total_sentences, risk),
                    method=METHOD_SENTENCE_SCAN,
                    sentence_hits=scan.hits,
                    total_sentences=reported_total_sentences,
                    capped=scan.capped,
                    profile=scan.profile,
                    occurrence_sentences=scan.occurrence_sentences,
                    occurrences=scan.occurrences,
                )
            )
            continue
        if not item.indexable:
            # No count exists to report, so report none — not a 0 that would read
            # as a measured absence (BL-164). The parts are the evidence offered
            # in its place.
            parts = tuple(PartCount(part, int(counts.get(part, 0))) for part in item.parts)
            verdicts.append(
                TermVerdict(
                    term=item.term,
                    normalized_term=item.key,
                    uses=None,
                    total_uses=total,
                    share=None,
                    flood_risk=UNKNOWN,
                    attested=None,
                    indexable=False,
                    measurement=UNAVAILABLE,
                    parts=parts,
                    note=_unindexable_note(item.term, parts),
                )
            )
            continue
        if item.measurement == COMPONENT_UPPER_BOUND:
            parts = tuple(PartCount(part, int(counts.get(part, 0))) for part in item.parts)
            uses = min((part.uses for part in parts), default=0)
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
                    attested=False if uses <= 0 else None,
                    indexable=True,
                    measurement=COMPONENT_UPPER_BOUND,
                    parts=parts,
                    note=_phrase_note(item.term, parts, uses),
                )
            )
            continue
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
                indexable=True,
                measurement=EXACT,
                parts=(),
                note=_note(item.term, uses, risk),
            )
        )
    return verdicts
