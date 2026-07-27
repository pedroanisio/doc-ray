"""Postgres persistence seam for explainable NER mention confidence.

The read half turns current mention rows plus tenant-scoped
``entity_frequency_stats`` into pure-domain facts.  The write half materializes
the resulting confidence and explanations back onto ``entity_mention``.  Both
operations are worker-only; request paths only consume the ranked result.

PALS's Law applies: the underlying NER labels are untrusted model output.  This
adapter stores an explainable triage score and never promotes it to truth.
"""

from __future__ import annotations

from dataclasses import dataclass

from doc_ray.adapters.persistence import DictConnection
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.domain.ner_corrections import Mention

_MENTIONS_SQL = """
    SELECT em.id AS mention_id,
           sp.char_start,
           sp.char_end,
           em.entity_type,
           em.surface,
           d.tenant_id,
           NOT EXISTS (
               SELECT 1
               FROM sentences sentence
               JOIN spans sentence_span ON sentence_span.id = sentence.span_id
               WHERE sentence.document_id = an.document_id
                 AND sentence_span.char_start <= sp.char_start
                 AND sentence_span.char_end >= sp.char_end
           ) AS crosses_sentence_boundary
    FROM entity_mention em
    JOIN annotations an ON an.id = em.annotation_id
    JOIN spans sp ON sp.id = em.span_id
    JOIN documents d ON d.id = an.document_id
    WHERE an.document_id = %s
    ORDER BY sp.char_start, sp.char_end, em.id
"""

_TENANT_ENTITY_STATS_SQL = """
    SELECT entity_text, entity_type, uses
    FROM entity_frequency_stats
    WHERE tenant_id = %s
    ORDER BY entity_text, entity_type
"""

_REGISTRY_FORMS_SQL = """
    SELECT DISTINCT form_norm
    FROM form_index
    WHERE form_norm = ANY(%s)
    ORDER BY form_norm
"""

_PERSIST_SQL = """
    UPDATE entity_mention
    SET confidence = %s,
        confidence_method = %s,
        confidence_reasons = %s
    WHERE id = %s
"""


@dataclass(frozen=True)
class MentionFacts:
    """Corpus observations required by the pure confidence scorer."""

    mention_id: str
    mention: Mention
    type_counts: dict[str, int]
    occurrence_count: int
    resolved: bool
    crosses_sentence_boundary: bool


@dataclass(frozen=True)
class StoredScore:
    """One validated confidence materialization for an entity mention."""

    mention_id: str
    confidence: float
    method: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        """Reject a score that the schema or review contract cannot represent."""
        if not self.mention_id.strip():
            raise ValueError("mention_id must not be blank")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.method.strip():
            raise ValueError("confidence method must not be blank")
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("at least one non-blank confidence reason is required")


def load_mention_facts(conn: DictConnection, document_id: str) -> list[MentionFacts]:
    """Load tenant-scoped scorer inputs for one document without a read-path aggregate.

    The corpus counts come from the D-024 ``entity_frequency_stats`` rollup.  Its
    exact surface rows are folded with :func:`form_norm`, the same grouping key
    used by ``entity_profiles`` and ``form_index``.
    """
    mention_rows = conn.execute(_MENTIONS_SQL, (document_id,)).fetchall()
    if not mention_rows:
        return []

    tenant_id = str(mention_rows[0]["tenant_id"])
    stat_rows = conn.execute(_TENANT_ENTITY_STATS_SQL, (tenant_id,)).fetchall()
    counts_by_form: dict[str, dict[str, int]] = {}
    for row in stat_rows:
        key = form_norm(str(row["entity_text"]))
        entity_type = str(row["entity_type"])
        counts = counts_by_form.setdefault(key, {})
        counts[entity_type] = counts.get(entity_type, 0) + int(row["uses"])

    keys = sorted({form_norm(str(row["surface"])) for row in mention_rows})
    registry_rows = conn.execute(_REGISTRY_FORMS_SQL, (keys,)).fetchall()
    resolved_forms = {str(row["form_norm"]) for row in registry_rows}

    facts: list[MentionFacts] = []
    for row in mention_rows:
        surface = str(row["surface"] or "")
        entity_type = str(row["entity_type"])
        key = form_norm(surface)
        type_counts = dict(counts_by_form.get(key) or {entity_type: 1})
        facts.append(
            MentionFacts(
                mention_id=str(row["mention_id"]),
                mention=Mention(
                    char_start=int(row["char_start"]),
                    char_end=int(row["char_end"]),
                    entity_type=entity_type,
                    surface=surface,
                ),
                type_counts=type_counts,
                occurrence_count=sum(type_counts.values()),
                resolved=key in resolved_forms,
                crosses_sentence_boundary=bool(row["crosses_sentence_boundary"]),
            )
        )
    return facts


def persist_scores(conn: DictConnection, scores: list[StoredScore]) -> int:
    """Materialize ``scores`` idempotently; return the number of rows attempted."""
    for stored in scores:
        conn.execute(
            _PERSIST_SQL,
            (
                stored.confidence,
                stored.method,
                list(stored.reasons),
                stored.mention_id,
            ),
        )
    return len(scores)
