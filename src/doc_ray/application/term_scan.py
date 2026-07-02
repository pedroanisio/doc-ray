"""Stage 1.5 — registry-driven term scan (deterministic terminology mentions).

NER (Stage 1) only tags proper-noun-shaped spans, so in technical literature the
domain vocabulary — lowercase common nouns like "uncial" or "parchment" — never
becomes a mention, and the registry entries seeded for it are unreachable. This
stage closes the gap deterministically: scan the document text for surfaces the
remissive index already knows and emit ``entity_mention`` rows under a dedicated
``layer='term'`` annotation. It only *emits* mentions; Stage 2 links them through
the one existing pathway, so ambiguous forms still become residue for the
frontier/review instead of being force-linked.

The scan is registry-driven, so it is field-agnostic by construction: seeding a
new domain (Stage 0) or a frontier write-back (Stage 6) immediately makes that
vocabulary detectable — no model, no retraining, no LLM cost.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import Any

import psycopg

from doc_ray.adapters import persistence
from doc_ray.domain.ulid import new_ulid_str

_Conn = psycopg.Connection[dict[str, Any]]

#: Tool tag recorded on the ``layer='term'`` annotation this stage owns.
_TOOL = "registry_scan"

#: Single-token forms shorter than this never match (alias junk: "BM", "pen", "Rom").
_MIN_SINGLE_TOKEN_LEN = 4

#: Common English words that occur as KB aliases but are unusable as anchors —
#: matching them would flood documents with false mentions. Deliberately small:
#: the length guard already removes most junk; this catches frequent >=4-char words.
_STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "will", "would", "there", "their",
    "about", "which", "when", "were", "been", "they", "them", "then", "than",
    "some", "what", "also", "more", "most", "over", "such", "only", "time",
    "said", "very", "into", "upon", "these", "those", "does", "made", "make",
    "many", "much", "each", "other", "after", "before",
})  # fmt: skip


@dataclass(frozen=True)
class TermForm:
    """One scannable registry surface with the entity type its canonical carries."""

    surface: str
    entity_type: str


@dataclass(frozen=True)
class TermHit:
    """One occurrence of a registry surface in the document text."""

    char_start: int
    char_end: int
    surface: str
    entity_type: str


def _eligible(form: TermForm) -> bool:
    """Return whether *form* is safe to anchor on (length/stopword/letter guards)."""
    surface = form.surface.strip()
    if not any(ch.isalpha() for ch in surface):
        return False
    if surface.casefold() in _STOPWORDS:
        return False
    single_token = len(surface.split()) == 1
    return not (single_token and len(surface) < _MIN_SINGLE_TOKEN_LEN)


def _alternative(surface: str) -> str:
    """Compile one surface into a regex alternative (whitespace-flexible, case-scoped).

    All-caps surfaces are acronym-like and must match case-sensitively ("NASA",
    never "nasa"); everything else matches case-insensitively via a local flag
    group so both kinds coexist in a single pattern.
    """
    body = r"\s+".join(re.escape(part) for part in surface.split())
    return body if surface.isupper() else f"(?i:{body})"


def _build_pattern(forms: list[TermForm]) -> re.Pattern[str] | None:
    """Build the single alternation pattern, longest surface first (longest match wins)."""
    if not forms:
        return None
    ordered = sorted(forms, key=lambda f: len(f.surface), reverse=True)
    alternation = "|".join(_alternative(f.surface.strip()) for f in ordered)
    return re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")


def scan_text(text: str, forms: list[TermForm]) -> list[TermHit]:
    """Return every registry-surface occurrence in *text* (pure, deterministic).

    Matching is whole-word (lookaround boundaries), case-insensitive except for
    all-caps acronym forms, and whitespace-flexible inside multi-word surfaces.
    Overlaps resolve to the longest surface at the leftmost position.

    Args:
        text: The document text to scan.
        forms: Registry surfaces (with entity types) to look for.

    Returns:
        Hits in document order, offsets indexing directly into *text*.
    """
    eligible = [f for f in forms if _eligible(f)]
    pattern = _build_pattern(eligible)
    if pattern is None:
        return []
    type_of = {" ".join(f.surface.split()).casefold(): f.entity_type for f in eligible}
    hits: list[TermHit] = []
    for match in pattern.finditer(text):
        surface = match.group(0)
        key = " ".join(surface.split()).casefold()
        hits.append(
            TermHit(
                char_start=match.start(),
                char_end=match.end(),
                surface=surface,
                entity_type=type_of.get(key, "UNKNOWN"),
            )
        )
    return hits


def _registry_forms(conn: _Conn, langs: tuple[str, ...]) -> list[TermForm]:
    """Load one scannable form per distinct surface (best-confidence canonical's type)."""
    rows = conn.execute(
        "SELECT DISTINCT ON (lower(f.form_surface)) f.form_surface, c.entity_type "
        "FROM form_index f JOIN canonical c ON c.id = f.canonical_id "
        "WHERE f.lang = ANY(%s) "
        "ORDER BY lower(f.form_surface), f.confidence DESC, f.occurrences DESC",
        (list(langs),),
    ).fetchall()
    return [TermForm(str(r["form_surface"]), str(r["entity_type"])) for r in rows]


#: Universal POS tags a single-token term hit may carry. Terminology is nominal
#: (incl. adjectival uses like "uncial letters"); a hit tagged VERB/ADV/ADP/...
#: is ordinary usage of a polysemous alias ("a loose end hanging from..."), not
#: a mention of the entity. Hits without token info are kept (benefit of doubt).
_NOMINAL_UPOS = frozenset({"NOUN", "PROPN", "ADJ", "X"})


def _token_upos(conn: _Conn, document_id: str) -> dict[tuple[int, int], str]:
    """Return the document's ``(char_start, char_end) -> upos`` token map."""
    rows = conn.execute(
        "SELECT sp.char_start, sp.char_end, t.upos FROM tokens t "
        "JOIN annotations a ON a.id = t.annotation_id "
        "JOIN spans sp ON sp.id = t.span_id "
        "WHERE a.document_id = %s AND t.upos IS NOT NULL",
        (document_id,),
    ).fetchall()
    return {(int(r["char_start"]), int(r["char_end"])): str(r["upos"]) for r in rows}


def _is_nominal(hit: TermHit, upos_at: dict[tuple[int, int], str]) -> bool:
    """Return whether *hit* is usable as a term mention given the POS layer.

    Multi-word hits are strong anchors and always pass; a single-token hit passes
    unless its exactly-aligned token carries a non-nominal tag.
    """
    if len(hit.surface.split()) > 1:
        return True
    upos = upos_at.get((hit.char_start, hit.char_end))
    return upos is None or upos in _NOMINAL_UPOS


def _covered_intervals(conn: _Conn, document_id: str) -> list[tuple[int, int]]:
    """Return sorted span intervals already claimed by other mention layers."""
    rows = conn.execute(
        "SELECT sp.char_start, sp.char_end FROM entity_mention em "
        "JOIN annotations a ON a.id = em.annotation_id "
        "JOIN spans sp ON sp.id = em.span_id "
        "WHERE a.document_id = %s AND a.layer <> 'term' "
        "ORDER BY sp.char_start, sp.char_end",
        (document_id,),
    ).fetchall()
    return [(int(r["char_start"]), int(r["char_end"])) for r in rows]


def _overlaps(intervals: list[tuple[int, int]], start: int, end: int) -> bool:
    """Return whether ``[start, end)`` intersects any of the sorted *intervals*."""
    idx = bisect_right(intervals, (start, end))
    if idx < len(intervals) and intervals[idx][0] < end:
        return True
    return any(intervals[i][1] > start for i in range(max(0, idx - 8), idx))


def scan_document(conn: _Conn, document_id: str, *, langs: tuple[str, ...] = ("", "en")) -> int:
    """Scan one document for registry terminology and persist the term mentions.

    Replaces the document's previous ``layer='term'`` annotation (idempotent —
    cascades old mentions and their links), suppresses hits already covered by
    another mention layer (NER owns those spans), and writes the remainder as
    ``entity_mention`` rows. Linking is deliberately left to Stage 2.

    Args:
        conn: Open autocommit connection (``dict_row``).
        document_id: Document to scan.
        langs: ``form_index.lang`` values eligible for this document.

    Returns:
        The number of term mentions written.
    """
    row = conn.execute(
        "SELECT full_text FROM document_content WHERE document_id = %s", (document_id,)
    ).fetchone()
    if row is None:
        return 0
    conn.execute(
        "DELETE FROM annotations WHERE document_id = %s AND layer = 'term' AND tool = %s",
        (document_id, _TOOL),
    )
    covered = _covered_intervals(conn, document_id)
    upos_at = _token_upos(conn, document_id)
    hits = [
        h
        for h in scan_text(str(row["full_text"]), _registry_forms(conn, langs))
        if _is_nominal(h, upos_at) and not _overlaps(covered, h.char_start, h.char_end)
    ]
    if not hits:
        return 0
    with conn.transaction():
        ann = new_ulid_str()
        conn.execute(
            "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s, %s, 'term', %s)",
            (ann, document_id, _TOOL),
        )
        span_map = persistence.ensure_spans(
            conn, document_id, [(h.char_start, h.char_end) for h in hits]
        )
        with conn.cursor() as cur:
            copy_sql = "COPY entity_mention (id, annotation_id, span_id, entity_type) FROM STDIN"
            with cur.copy(copy_sql) as cp:
                for h in hits:
                    cp.write_row(
                        (new_ulid_str(), ann, span_map[(h.char_start, h.char_end)], h.entity_type)
                    )
    return len(hits)
