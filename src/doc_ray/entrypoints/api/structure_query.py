"""GraphQL ``StructureQueries`` mixin — document outline + reconciled entities (BL-146).

Split out of the ``Query`` root (D-015), the same pattern as ``VisualQueries``:
``Query`` inherits this mixin so these resolvers live in their own module. Both
fields clamp their page size and delegate to the async structure read adapter,
RLS-scoped like every other read.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import persistence_read_structure as structure_read
from doc_ray.adapters import persistence_read_terms as terms_read
from doc_ray.domain import term_frequency as tf
from doc_ray.entrypoints.api.ids import clean_uuid
from doc_ray.entrypoints.api.structure_types import DocumentOutline, EntityProfile, TermFrequency
from doc_ray.entrypoints.api.types import Info

#: Returned for a malformed id — indistinguishable, by design, from a document
#: the caller may not read.
_UNREADABLE = {
    "source": "empty",
    "total_sentences": 0,
    "entry_count": 0,
    "note": "No such document, or it is not readable with this token.",
    "entries": [],
}


@strawberry.type
class StructureQueries:
    """Read fields that make the rest of a document reachable."""

    @strawberry.field
    async def document_outline(
        self, info: Info, document_id: strawberry.ID, max_entries: int = 50
    ) -> DocumentOutline:
        """Map a document into addressable ordinal ranges for ranged reading.

        Prefers detected headings; falls back to an even split of the sentences
        when a document has none, reporting which via ``source`` so a navigation
        grid is never mistaken for a table of contents.
        """
        doc_id = clean_uuid(str(document_id))
        if doc_id is None:
            return DocumentOutline.from_row(_UNREADABLE)
        row = await structure_read.document_outline(
            info.context.conn, doc_id, max_entries=max_entries
        )
        return DocumentOutline.from_row(row)

    @strawberry.field
    async def entity_profiles(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        entity_type: str | None = None,
        limit: int = 50,
    ) -> list[EntityProfile]:
        """Entity frequencies reconciled to one row per entity, type spread included.

        ``entityFrequency`` returns raw NER, which splits one entity across types
        and spellings. This groups those rows and reports the disagreement as
        ``typeConsistency`` instead of leaving the caller to notice it.
        """
        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = clean_uuid(str(document_id))
            if doc_id is None:  # malformed -> no matches, not a DB error
                return []
        rows = await structure_read.entity_profiles(
            ctx.conn,
            document_id=doc_id,
            entity_type=entity_type,
            limit=ctx.page_size(limit),
        )
        return [EntityProfile.from_row(row) for row in rows]

    @strawberry.field
    async def term_frequency(
        self, info: Info, terms: list[str], document_id: strawberry.ID | None = None
    ) -> list[TermFrequency]:
        """How common candidate search terms are — a flood-risk pre-flight before searching.

        Returns a ``floodRisk`` verdict (``low``/``moderate``/``high``/``unknown``) per
        term from its share of the corpus-wide ``lexemeStats`` rollup, so an agent can
        pick a rarer term instead of flooding a search. A term the lexeme index cannot
        hold (``shared_buffers``, ``COVID-19``) comes back ``indexable: false`` with null
        counts rather than a fabricated zero (BL-164). Corpus-wide only for now: passing
        ``documentId`` fails with a typed error rather than returning corpus counts
        mislabelled as document-scoped.

        A term the lexeme index cannot count exactly — a whitespace phrase, an
        underscore compound, an alphanumeric — is **measured** instead of declined:
        the server counts the sentences matching the same literal-substring rule
        ``searchSentences`` applies, bounded by a cap, and returns
        ``method: "sentence_scan"`` with ``sentenceHits``/``totalSentences``/``capped``.
        The rarest-component upper bound rides along as secondary evidence.
        """
        if document_id is not None:
            raise ValueError(
                "term frequency is corpus-wide only; document-scoped counts are not "
                "available yet — omit documentId"
            )
        try:
            prepared = tf.prepare_terms(terms)
        except tf.InvalidTerms as exc:
            raise ValueError(str(exc)) from exc
        counts, total_uses = await terms_read.term_frequencies(
            info.context.conn, tf.lookup_keys(prepared)
        )
        # Only the terms the lexeme index cannot answer exactly are scanned, so a
        # batch of ordinary single words still costs exactly one statement.
        scan_targets = {
            item.key: tf.scan_text(item)
            for item in prepared
            if item.key in set(tf.scan_keys(prepared))
        }
        scans, total_sentences = await terms_read.sentence_hit_counts(
            info.context.conn, scan_targets, cap=tf.SENTENCE_HIGH_HITS
        )
        verdicts = tf.build_verdicts(
            prepared, counts, total_uses, scans=scans, total_sentences=total_sentences
        )
        return [TermFrequency.from_verdict(verdict) for verdict in verdicts]
