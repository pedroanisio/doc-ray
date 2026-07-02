"""Seed suggestion service — mine a document's candidate domain terms and resolve them.

The automatable 90% of domain seeding: find what a document keeps talking about
that the registry does not know yet, and pin each candidate to an exact Wikidata
identity via its Wikipedia article (pageprops — no fuzzy-search top hits). The
unautomatable 10% — judging whether "Albertus" means the typeface or the friar —
stays human: everything lands in ``seed_suggestion`` as ``pending`` for curation;
nothing touches the canonical registry here.

Two deterministic candidate sources:

- **mention** — surfaces of not-yet-linked entity mentions (NER-shaped names),
  excluding non-referential types, grouped by their most frequent casing.
- **lexeme** — frequent NOUN lemmas (the technical-vocabulary channel that NER
  never tags), thresholded higher because prose nouns are plentiful.

Both skip terms the remissive index already resolves — the flywheel means a
known form needs no new seed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from doc_ray.adapters import seed_suggestion_store as store
from doc_ray.application.resolution import NON_REFERENTIAL_TYPES
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.ports.knowledge_base import KnowledgeBase

_Conn = psycopg.Connection[dict[str, Any]]

_MENTION_SQL = """
SELECT surface AS term, count(*) AS occurrences
FROM (
    SELECT substring(dc.full_text FROM sp.char_start + 1 FOR sp.char_end - sp.char_start)
             AS surface
    FROM entity_mention em
    JOIN annotations a      ON a.id = em.annotation_id
    JOIN spans sp           ON sp.id = em.span_id
    JOIN document_content dc ON dc.document_id = a.document_id
    LEFT JOIN entity_link el ON el.mention_id = em.id AND el.scheme = 'canonical'
    WHERE a.document_id = %s AND el.mention_id IS NULL
      AND em.entity_type <> ALL(%s)
) s
WHERE length(surface) >= 3 AND surface ~ '[[:alpha:]]'
GROUP BY surface
HAVING count(*) >= %s
ORDER BY count(*) DESC
LIMIT %s
"""

_LEXEME_SQL = """
SELECT l.lemma AS term, count(*) AS occurrences
FROM lexeme_occurrences o
JOIN annotations a ON a.id = o.annotation_id
JOIN lexemes l     ON l.uid = o.lexeme_uid
WHERE a.document_id = %s AND l.pos = 'NOUN' AND length(l.lemma) >= 4
GROUP BY l.lemma
HAVING count(*) >= %s
ORDER BY count(*) DESC
LIMIT %s
"""


class TitleResolver(Protocol):
    """The slice of the Wikipedia client this service needs (tests inject stubs)."""

    def qids_for_titles(self, titles: list[str]) -> dict[str, str | None]:
        """Resolve article titles to QIDs (``None`` = no usable article)."""
        ...  # pragma: no cover - protocol signature


@dataclass(frozen=True)
class SeedCandidate:
    """One mined candidate term, before KB resolution."""

    term: str
    occurrences: int
    source: str


def _known(conn: _Conn, term: str) -> bool:
    """Return whether the remissive index already resolves *term*."""
    row = conn.execute(
        "SELECT 1 FROM form_index WHERE form_norm = %s LIMIT 1", (form_norm(term),)
    ).fetchone()
    return row is not None


def mine_candidates(
    conn: _Conn,
    document_id: str,
    *,
    limit: int = 40,
    min_mentions: int = 3,
    min_lexemes: int = 10,
) -> list[SeedCandidate]:
    """Mine a document's candidate seed terms (mention surfaces + noun lemmas).

    Args:
        conn: Open autocommit connection (``dict_row``).
        document_id: Document to mine.
        limit: Overall candidate ceiling (frequency-ordered).
        min_mentions: Occurrence floor for the mention source.
        min_lexemes: Occurrence floor for the noun-lemma source.

    Returns:
        Candidates ordered by frequency, deduplicated case-insensitively
        (mention source wins), each not yet resolvable via the remissive index.
    """
    rows = conn.execute(
        _MENTION_SQL, (document_id, list(NON_REFERENTIAL_TYPES), min_mentions, limit)
    ).fetchall()
    candidates = [SeedCandidate(str(r["term"]), int(r["occurrences"]), "mention") for r in rows]
    rows = conn.execute(_LEXEME_SQL, (document_id, min_lexemes, limit)).fetchall()
    candidates += [SeedCandidate(str(r["term"]), int(r["occurrences"]), "lexeme") for r in rows]

    seen: set[str] = set()
    out: list[SeedCandidate] = []
    for cand in sorted(candidates, key=lambda c: (c.source != "mention", -c.occurrences)):
        key = cand.term.casefold()
        if key in seen or _known(conn, cand.term):
            continue
        seen.add(key)
        out.append(cand)
    out.sort(key=lambda c: -c.occurrences)
    return out[:limit]


def suggest_for_document(
    conn: _Conn,
    wiki: TitleResolver,
    kb: KnowledgeBase,
    document_id: str,
    *,
    limit: int = 40,
    min_mentions: int = 3,
    min_lexemes: int = 10,
) -> dict[str, int]:
    """Mine, resolve, and persist this document's seed suggestions (all ``pending``).

    Every candidate is kept — resolved ones carry the KB identity (QID, label,
    description, type) the curator audits; unresolved ones stay visible so a
    human can spot terms worth a manual QID. Decided rows are never reverted.

    Args:
        conn: Open autocommit connection (``dict_row``).
        wiki: Title -> QID resolver (Wikipedia pageprops).
        kb: Knowledge base for label/description/type enrichment.
        document_id: Document to suggest seeds for.
        limit: Candidate ceiling.
        min_mentions: Occurrence floor for the mention source.
        min_lexemes: Occurrence floor for the noun-lemma source.

    Returns:
        ``{"suggested": total_rows_written, "resolved": rows_with_a_qid}``.
    """
    candidates = mine_candidates(
        conn, document_id, limit=limit, min_mentions=min_mentions, min_lexemes=min_lexemes
    )
    qids = wiki.qids_for_titles([c.term for c in candidates])
    resolved = 0
    for cand in candidates:
        qid = qids.get(cand.term)
        entry = kb.get(qid) if qid else None
        if entry is not None:
            resolved += 1
        store.upsert_suggestion(
            conn,
            document_id=document_id,
            term=cand.term,
            occurrences=cand.occurrences,
            source=cand.source,
            qid=entry.kb_id if entry else None,
            label=entry.label if entry else None,
            description=entry.description if entry else None,
            entity_type=entry.entity_type if entry else None,
        )
    return {"suggested": len(candidates), "resolved": resolved}
