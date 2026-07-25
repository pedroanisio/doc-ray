"""The GraphQL ``Query`` root — read-only entry points over the store.

Every resolver is thin: it clamps its page size and delegates to the async read
adapter (:mod:`doc_ray.adapters.persistence_read`), then maps rows to types. No SQL
lives here.
"""

from __future__ import annotations

import strawberry

from doc_ray.adapters import document_metadata_suggestion_store as metadata_suggestions
from doc_ray.adapters import ner_corrections_store, security_audit_read
from doc_ray.adapters import persistence_canonical_read as canonical_read
from doc_ray.adapters import persistence_read as read
from doc_ray.adapters import queue_postgres as queue
from doc_ray.adapters import seed_suggestion_store as seeds
from doc_ray.application import billing
from doc_ray.entrypoints.api.audit_types import DocumentMetadataChange, DocumentMetadataSuggestion
from doc_ray.entrypoints.api.billing_types import BillingEvent, BillingStatus
from doc_ray.entrypoints.api.canonical_types import (
    CanonicalEntity,
    ResolutionReview,
    UnresolvedMention,
)
from doc_ray.entrypoints.api.collection_query import CollectionQueries
from doc_ray.entrypoints.api.ids import clean_uuid as _clean_uuid
from doc_ray.entrypoints.api.mutation_guards import _require_admin_read
from doc_ray.entrypoints.api.security_audit import record_audit_event
from doc_ray.entrypoints.api.security_audit_types import SecurityAuditEvent
from doc_ray.entrypoints.api.structure_query import StructureQueries
from doc_ray.entrypoints.api.types import (
    CorpusManifest,
    Document,
    DocumentHit,
    DocumentShare,
    EntityFrequency,
    EntityMention,
    EntityMentionCorrection,
    Info,
    Job,
    Lexeme,
    SeedSuggestion,
    SentenceHit,
)
from doc_ray.entrypoints.api.visual_query import VisualQueries


@strawberry.type
class Query(VisualQueries, StructureQueries, CollectionQueries):
    """The GraphQL query root."""

    @strawberry.field
    def version(self) -> str:
        """Return the running service's semantic version (``doc_ray.__version__``)."""
        from doc_ray import __version__

        return __version__

    @strawberry.field
    async def documents(
        self,
        info: Info,
        limit: int = 50,
        after: str | None = None,
        doc_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> list[Document]:
        """Return a bounded page of document summaries, optionally filtered.

        ``tags`` matches a document that has any of the given tags (OR semantics).
        """
        ctx = info.context
        rows = await read.list_documents(
            ctx.conn,
            limit=ctx.page_size(limit),
            after=after,
            doc_type=doc_type,
            status=status,
            tags=tags,
        )
        return [Document.from_row(r) for r in rows]

    @strawberry.field
    async def corpus_manifest(self, info: Info, shard_count: int) -> CorpusManifest:
        """Return the shard map for partitioning the visible corpus into *shard_count* parts.

        Deterministic and RLS-scoped: per-shard document counts (which sum to
        ``totalDocuments``) let an orchestrator fan out one worker per shard and
        verify complete coverage. Pair with ``corpusShard`` to pull each part.
        """
        if shard_count < 1:
            raise ValueError("shardCount must be >= 1")
        ctx = info.context
        rows = await read.corpus_shard_counts(ctx.conn, shard_count)
        return CorpusManifest.from_rows(shard_count, rows)

    @strawberry.field
    async def corpus_shard(
        self,
        info: Info,
        shard_index: int,
        shard_count: int,
        limit: int = 50,
        after: str | None = None,
    ) -> list[Document]:
        """Return one shard's document summaries — a keyset-paged slice of the corpus.

        Documents are partitioned by ``hashtext(id) mod shardCount``; this returns
        those in shard ``shardIndex`` (0-based), newest first. Walk ``after``
        cursors to exhaust a shard; run shards ``0..shardCount-1`` (in parallel) to
        cover the whole corpus, disjointly. RLS-scoped to the caller's own corpus.
        """
        if shard_count < 1:
            raise ValueError("shardCount must be >= 1")
        if not 0 <= shard_index < shard_count:
            raise ValueError("shardIndex must be in [0, shardCount)")
        ctx = info.context
        rows = await read.list_documents(
            ctx.conn,
            limit=ctx.page_size(limit),
            after=after,
            shard_index=shard_index,
            shard_count=shard_count,
        )
        return [Document.from_row(r) for r in rows]

    @strawberry.field
    async def document(self, info: Info, id: strawberry.ID) -> Document | None:
        """Return the document with *id*, or ``None`` if not found (or *id* is malformed)."""
        doc_id = _clean_uuid(str(id))
        if doc_id is None:
            return None
        ctx = info.context
        row = await read.get_document(ctx.conn, doc_id)
        if row is not None:
            await record_audit_event(
                ctx.conn,
                principal=ctx.principal,
                action="graphql.document.read",
                target_type="document",
                target_id=doc_id,
                outcome="success",
            )
        return Document.from_row(row) if row is not None else None

    @strawberry.field
    async def document_shares(self, info: Info, id: strawberry.ID) -> list[DocumentShare]:
        """List who *id* is shared with (empty if the caller may not see the grants).

        ``document_acl``'s RLS (migration 108) scopes the rows: a sharer sees the
        grants they manage, a grantee sees their own. So a principal who can
        neither share nor was granted the document gets an empty list rather than
        an error, and the query never reveals another document's ACL.
        """
        doc_id = _clean_uuid(str(id))
        if doc_id is None:
            return []
        ctx = info.context
        rows = await read.list_document_shares(ctx.conn, doc_id)
        return [DocumentShare.from_row(row) for row in rows]

    @strawberry.field
    async def audit_events(
        self,
        info: Info,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        occurred_after: str | None = None,
        occurred_before: str | None = None,
        source_ip: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> list[SecurityAuditEvent]:
        """Return tenant-admin-visible audit events matching the supplied filters."""
        ctx = info.context
        await security_audit_read.require_audit_admin(ctx.conn)
        rows = await security_audit_read.list_audit_events(
            ctx.conn,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            source_ip=source_ip,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [SecurityAuditEvent.from_row(r) for r in rows]

    @strawberry.field
    async def billing_status(self, info: Info) -> BillingStatus:
        """Return the current tenant's billing state; tenant-admin only."""
        ctx = info.context
        await _require_admin_read(ctx, action="query.billing_status")
        settings = ctx.settings
        if settings is None and ctx.request is not None:
            settings = ctx.request.app.state.settings
        if settings is None:
            raise RuntimeError("billing settings are unavailable")
        row = await billing.billing_status(ctx.conn, settings)
        return BillingStatus.from_row(row)

    @strawberry.field
    async def billing_events(self, info: Info, limit: int = 25) -> list[BillingEvent]:
        """Return recent Stripe webhook payment activity; tenant-admin only."""
        ctx = info.context
        await _require_admin_read(ctx, action="query.billing_events")
        rows = await billing.billing_events(ctx.conn, limit=ctx.page_size(limit))
        return [BillingEvent.from_row(row) for row in rows]

    @strawberry.field
    async def document_metadata_history(
        self, info: Info, document_id: strawberry.ID
    ) -> list[DocumentMetadataChange]:
        """Return a document's title/description/tags edit history, oldest first."""
        doc_id = _clean_uuid(str(document_id))
        if doc_id is None:
            return []
        rows = await read.get_metadata_history(info.context.conn, doc_id)
        return [DocumentMetadataChange.from_row(r) for r in rows]

    @strawberry.field
    async def document_metadata_suggestions(
        self, info: Info, document_id: strawberry.ID, status: str | None = None
    ) -> list[DocumentMetadataSuggestion]:
        """Return verified document metadata suggestions for human review."""
        doc_id = _clean_uuid(str(document_id))
        if doc_id is None:
            return []
        rows = await metadata_suggestions.list_for_document_async(
            info.context.conn, doc_id, status=status
        )
        return [DocumentMetadataSuggestion.from_row(r) for r in rows]

    @strawberry.field
    async def entity_mention_corrections(
        self, info: Info, document_id: strawberry.ID
    ) -> list[EntityMentionCorrection]:
        """Return a document's durable human NER corrections, oldest first (Block A).

        The audit log behind ``correctEntityMention``: RLS scopes it to documents
        the caller may read (migration 115). Empty for a malformed id.
        """
        doc_id = _clean_uuid(str(document_id))
        if doc_id is None:
            return []
        rows = await ner_corrections_store.list_correction_rows_async(info.context.conn, doc_id)
        return [EntityMentionCorrection.from_row(r) for r in rows]

    @strawberry.field
    async def search_sentences(
        self,
        info: Info,
        term: str,
        document_id: strawberry.ID | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> list[SentenceHit]:
        """Return sentence hits for *term* (bounded page).

        Pass ``documentId`` to search within one document, and ``after`` to
        enumerate all matches for a common term page by page.
        """
        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = _clean_uuid(str(document_id))
            if doc_id is None:  # malformed -> no matches, not a DB error
                return []
        rows = await read.search_sentences(
            ctx.conn,
            term,
            document_id=doc_id,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [SentenceHit.from_row(r) for r in rows]

    @strawberry.field
    async def full_text_search(self, info: Info, query: str, limit: int = 25) -> list[DocumentHit]:
        """Return full-text document hits for *query* (bounded page)."""
        ctx = info.context
        rows = await read.fulltext_search(ctx.conn, query, limit=ctx.page_size(limit))
        return [DocumentHit.from_row(r) for r in rows]

    @strawberry.field
    async def lexeme(self, info: Info, uid: strawberry.ID) -> Lexeme | None:
        """Return the lexeme with *uid*, or ``None`` if it does not exist."""
        row = await read.get_lexeme(info.context.conn, str(uid))
        return Lexeme.from_row(row) if row is not None else None

    @strawberry.field
    async def lexemes(
        self,
        info: Info,
        q: str | None = None,
        pos: str | None = None,
        lang: str | None = None,
        document_id: strawberry.ID | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> list[Lexeme]:
        """Return a bounded page of filtered lexemes.

        With ``documentId``, only lexemes occurring in that document are returned
        and ``uses`` counts occurrences there — a per-document vocabulary table
        instead of corpus-global totals.
        """
        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = _clean_uuid(str(document_id))
            if doc_id is None:  # malformed -> no matches, not a DB error
                return []
        rows = await read.list_lexemes(
            ctx.conn,
            q=q,
            pos=pos,
            lang=lang,
            document_id=doc_id,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [Lexeme.from_row(r) for r in rows]

    @strawberry.field
    async def entities(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> list[EntityMention]:
        """Return named-entity mentions (NER layer), filterable by document and type."""
        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = _clean_uuid(str(document_id))
            if doc_id is None:  # malformed -> no matches, not a DB error
                return []
        rows = await read.list_entities(
            ctx.conn,
            document_id=doc_id,
            entity_type=entity_type,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [EntityMention.from_row(r) for r in rows]

    @strawberry.field
    async def entity_frequency(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        entity_type: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> list[EntityFrequency]:
        """Return entity mention frequencies — the character/place/org table."""
        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = _clean_uuid(str(document_id))
            if doc_id is None:  # malformed -> no matches, not a DB error
                return []
        rows = await read.entity_frequency(
            ctx.conn,
            document_id=doc_id,
            entity_type=entity_type,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [EntityFrequency.from_row(r) for r in rows]

    @strawberry.field
    async def job(self, info: Info, id: strawberry.ID) -> Job | None:
        """Return a background job's status by id, or ``None`` if unknown."""
        row = await queue.status_async(info.context.conn, str(id))
        return Job.from_row(row) if row is not None else None

    @strawberry.field
    async def jobs(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        limit: int = 20,
        after: str | None = None,
    ) -> list[Job]:
        """Return recent jobs, newest first — a document's pipeline progress view."""
        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = _clean_uuid(str(document_id))
            if doc_id is None:  # malformed -> no jobs, not a DB error
                return []
        rows = await queue.list_async(
            ctx.conn, document_id=doc_id, limit=ctx.page_size(limit), after=after
        )
        return [Job.from_row(r) for r in rows]

    @strawberry.field
    async def seed_suggestions(
        self, info: Info, document_id: strawberry.ID, status: str | None = None
    ) -> list[SeedSuggestion]:
        """Return a document's seed suggestions (unresolved last, then by frequency)."""
        doc_id = _clean_uuid(str(document_id))
        if doc_id is None:  # malformed -> no suggestions, not a DB error
            return []
        rows = await seeds.list_for_document_async(info.context.conn, doc_id, status=status)
        return [SeedSuggestion.from_row(r) for r in rows]

    @strawberry.field
    async def canonical(self, info: Info, id: int) -> CanonicalEntity | None:
        """Return one canonical registry entity by numeric id."""
        row = await canonical_read.get_canonical(info.context.conn, id)
        return CanonicalEntity.from_row(row) if row is not None else None

    @strawberry.field
    async def canonical_search(
        self, info: Info, q: str = "", limit: int = 50, after: str | None = None
    ) -> list[CanonicalEntity]:
        """Search canonical entities by preferred label, Wikidata id, or form."""
        ctx = info.context
        rows = await canonical_read.search_canonical(
            ctx.conn, q, limit=ctx.page_size(limit), after=after
        )
        return [CanonicalEntity.from_row(r) for r in rows]

    @strawberry.field
    async def unresolved_mentions(
        self, info: Info, document_id: strawberry.ID, limit: int = 100, after: str | None = None
    ) -> list[UnresolvedMention]:
        """Return entity mentions in a document that lack a canonical link."""
        ctx = info.context
        doc_id = _clean_uuid(str(document_id))
        if doc_id is None:  # malformed -> no mentions, not a DB error
            return []
        rows = await canonical_read.list_unresolved_mentions(
            ctx.conn, doc_id, limit=ctx.page_size(limit), after=after
        )
        return [UnresolvedMention.from_row(r) for r in rows]

    @strawberry.field
    async def resolution_review(
        self,
        info: Info,
        document_id: strawberry.ID | None = None,
        status: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> list[ResolutionReview]:
        """Return resolution-review rows, optionally scoped by document and status."""
        ctx = info.context
        doc_id: str | None = None
        if document_id is not None:
            doc_id = _clean_uuid(str(document_id))
            if doc_id is None:  # malformed -> no matches, not a DB error
                return []
        rows = await canonical_read.list_resolution_review(
            ctx.conn,
            document_id=doc_id,
            status=status,
            limit=ctx.page_size(limit),
            after=after,
        )
        return [ResolutionReview.from_row(r) for r in rows]
