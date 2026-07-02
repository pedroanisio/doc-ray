"""Stage 3 — resolve the Stage-2 residue with the frontier resolver (D-018/D-019).

For each residue mention the resolver returns a validated, constrained verdict
(the adapter guarantees SELECT ids are members of the offered candidate set —
PALS's Law). This service applies the decision policy:

- **SELECT / NEW at or above the threshold** — commit: write the mention→canonical
  edge (and, for NEW, mint the canonical), then write every observed form back to
  the remissive index (Stage 6) so the next occurrence resolves by lookup and the
  frontier model never sees it again — the flywheel.
- **ABSTAIN or low confidence** — never committed; queued in ``resolution_review``
  for a human (Stage 5), with the offered candidate set preserved for audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from doc_ray.adapters import canonical_store as store
from doc_ray.application.canonicalize import ResidueMention
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.resolver import EntityResolver, Resolution, ResolutionKind

_Conn = psycopg.Connection[dict[str, Any]]

#: Verdicts at or above this confidence are committed; below it they go to review.
AUTO_COMMIT_THRESHOLD = 0.8

#: source_types tag for forms learned from the corpus via the resolver.
_SOURCE = "corpus"

#: NER labels that are quantities/time expressions, not referential entities —
#: they never reach the frontier (pure cost) and are neither linked nor reviewed.
NON_REFERENTIAL_TYPES = frozenset(
    {"CARDINAL", "ORDINAL", "DATE", "TIME", "PERCENT", "MONEY", "QUANTITY"}
)


@dataclass(frozen=True)
class ResolutionOutcome:
    """Counts from one resolution pass over a document's residue.

    Attributes:
        linked: Mentions committed to an existing canonical (SELECT).
        created: New canonicals minted and linked (NEW).
        review: Mentions queued for human review (abstain / low confidence).
        skipped: Non-referential mentions filtered before the resolver.
    """

    linked: int = 0
    created: int = 0
    review: int = 0
    skipped: int = 0


def _write_back(
    conn: _Conn, canonical_id: int, resolution: Resolution, *, surface: str, preferred: str | None
) -> None:
    """Stage 6: register every observed form for the entity in the remissive index."""
    if preferred:
        store.upsert_form(
            conn,
            form_norm=form_norm(preferred),
            form_surface=preferred,
            canonical_id=canonical_id,
            form_type="name",
            confidence=resolution.confidence,
            occurrences=1,
            source_type=_SOURCE,
        )
    if surface.strip() and surface != preferred:
        store.upsert_form(
            conn,
            form_norm=form_norm(surface),
            form_surface=surface,
            canonical_id=canonical_id,
            form_type="alias",
            confidence=resolution.confidence,
            occurrences=1,
            source_type=_SOURCE,
        )
    for alias in resolution.harvested_aliases:
        if alias != surface and alias != preferred:
            store.upsert_form(
                conn,
                form_norm=form_norm(alias),
                form_surface=alias,
                canonical_id=canonical_id,
                form_type="alias",
                confidence=resolution.confidence,
                source_type=_SOURCE,
            )


def _queue_review(
    conn: _Conn, mention: ResidueMention, resolution: Resolution, *, resolver_name: str
) -> None:
    """Queue an uncommitted verdict for human review, preserving the candidate set."""
    candidates = [
        {"canonical_id": c.canonical_id, "preferred": c.preferred, "entity_type": c.entity_type}
        for c in mention.candidates
    ]
    conn.execute(
        "INSERT INTO resolution_review "
        "  (id, mention_id, candidates, proposed_canonical_id, confidence, status, reason) "
        "VALUES (%s, %s, %s, %s, %s, 'pending', %s)",
        (
            new_ulid_str(),
            mention.mention_id,
            Jsonb(candidates),
            resolution.canonical_id,
            resolution.confidence,
            f"{resolution.kind.value} by {resolver_name}",
        ),
    )


def resolve_residue(
    conn: _Conn,
    resolver: EntityResolver,
    document_id: str,
    residue: list[ResidueMention],
    *,
    threshold: float = AUTO_COMMIT_THRESHOLD,
) -> ResolutionOutcome:
    """Resolve a document's residue mentions and apply the decision policy.

    Args:
        conn: Sync database connection (autocommit).
        resolver: The constrained-choice resolver (frontier model or stub).
        document_id: UUID of the owning document (for sentence context).
        residue: Mentions Stage 2 could not confidently resolve.
        threshold: Minimum confidence for auto-commit; below it -> review.

    Returns:
        Counts of linked / created / queued-for-review mentions.
    """
    resolver_name = getattr(resolver, "name", type(resolver).__name__)
    linked = created = review = skipped = 0
    for mention in residue:
        if mention.entity_type in NON_REFERENTIAL_TYPES:
            skipped += 1  # NER noise: never spend a frontier call on it
            continue
        sentence = (
            store.sentence_for_span(conn, document_id, mention.char_start, mention.char_end) or ""
        )
        resolution = resolver.resolve(mention.surface, sentence, mention.candidates)

        if resolution.kind is ResolutionKind.SELECT and resolution.confidence >= threshold:
            # The adapter guarantees canonical_id ∈ candidates (PALS), but assert anyway.
            assert resolution.canonical_id is not None
            store.link_mention(
                conn, mention.mention_id, resolution.canonical_id, confidence=resolution.confidence
            )
            _write_back(
                conn, resolution.canonical_id, resolution, surface=mention.surface, preferred=None
            )
            linked += 1
        elif resolution.kind is ResolutionKind.NEW and resolution.confidence >= threshold:
            assert resolution.new_preferred is not None
            # The resolver's proposed class (validated against a closed enum) wins over
            # the mention's NER tag — it sees context and can correct upstream mistypes.
            canonical_id = store.create_canonical(
                conn,
                preferred=resolution.new_preferred,
                entity_type=resolution.new_entity_type or mention.entity_type,
            )
            store.link_mention(
                conn, mention.mention_id, canonical_id, confidence=resolution.confidence
            )
            _write_back(
                conn,
                canonical_id,
                resolution,
                surface=mention.surface,
                preferred=resolution.new_preferred,
            )
            created += 1
        else:
            _queue_review(conn, mention, resolution, resolver_name=resolver_name)
            review += 1
    return ResolutionOutcome(linked=linked, created=created, review=review, skipped=skipped)
