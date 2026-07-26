"""GraphQL mutations for allusion edges (SLP Block E, issue #9, FR-3).

Three writes, mixed into the ``Mutation`` root (D-015), all guarded by
``_require_write_audited`` (write scope + billing entitlement) and audited on success
AND denial. ``entity_allusion`` is a **curation** table the API writes directly
(docray_client) — unlike Block D's derived fact tables. The per-document/per-lens
protection is enforced by the table's RLS (the caller may only write an edge justified
by a document they can write); this layer adds the scope/billing gate and a clean error.

An allusion is an EDGE, never an identity, and never transitive (see
:mod:`doc_ray.domain.allusions`). A machine proposal lands ``proposed`` for review.
"""

from __future__ import annotations

import uuid

import psycopg
import strawberry

from doc_ray.adapters import allusion_store
from doc_ray.adapters import persistence_allusion_read as allusion_read
from doc_ray.domain.allusions import STATUS_CONFIRMED, STATUS_REJECTED, validate_edge
from doc_ray.entrypoints.api.context import RequestContext
from doc_ray.entrypoints.api.mutation_guards import _audit_mutation_event, _require_write_audited
from doc_ray.entrypoints.api.types import AllusionEdge, Info

#: A human/agent drawing an allusion through the review API asserts it as a curator;
#: the deterministic bulk proposer (KIND_PROPOSE_ALLUSIONS) stamps 'heuristic:<v>'.
_API_METHOD = "human"


def _uuid(value: str, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValueError(f"unknown {label} {value}") from exc


async def _apply_status(
    ctx: RequestContext, allusion_id: strawberry.ID, status: str, action: str
) -> AllusionEdge:
    """Confirm/reject shared path (post-guard): RLS-enforced, audited on both outcomes."""
    aid = _uuid(str(allusion_id), "allusion")
    row = await allusion_store.set_allusion_status(
        ctx.conn, aid, status=status, reviewer=ctx.principal.principal_id
    )
    if row is None:
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="entity_allusion",
            target_id=aid,
            outcome="denied",
            details={"reason": "not_found_or_not_writable"},
        )
        raise ValueError(f"unknown allusion {allusion_id} (or not writable)")
    await _audit_mutation_event(
        ctx,
        action=action,
        target_type="entity_allusion",
        target_id=aid,
        outcome="success",
        details={"status": status},
    )
    return AllusionEdge.from_row(await allusion_read.hydrate_names(ctx.conn, row))


@strawberry.type
class AllusionMutations:
    """Propose/confirm/reject typed cross-lens allusion edges (Block E)."""

    @strawberry.mutation
    async def propose_allusion(
        self,
        info: Info,
        from_canonical_id: int,
        from_lens_id: strawberry.ID,
        to_canonical_id: int,
        to_lens_id: strawberry.ID,
        edge_type: str,
        evidence_document_id: strawberry.ID,
        confidence: float | None = None,
        rationale: str | None = None,
    ) -> AllusionEdge:
        """Propose a typed cross-lens allusion — a link, NOT an identity claim (Block E, #9).

        Records that ``fromCanonicalId`` in ``fromLensId`` *alludes to / depicts /
        references / is adapted from / inspired by* ``toCanonicalId`` in ``toLensId``.
        This changes **nothing** about entity resolution — the two entities stay
        distinct; the edge only names their relationship. ``edgeType`` must be one of
        ``alludes_to``, ``depicts``, ``references``, ``adapted_from``, ``inspired_by``.
        ``evidenceDocumentId`` cites the document the allusion appears in and anchors
        the write (you may only propose an edge justified by a document you can write).
        Idempotent (a duplicate returns the existing edge); lands ``status='proposed'``
        until confirmed (PALS's Law). Never traverse an edge as identity or chain edges
        transitively.
        """
        ctx = info.context
        action = "mutation.propose_allusion"
        await _require_write_audited(ctx, action=action)
        from_lens = _uuid(str(from_lens_id), "lens")
        to_lens = _uuid(str(to_lens_id), "lens")
        evidence = _uuid(str(evidence_document_id), "document")
        try:
            validate_edge(
                from_canonical_id=from_canonical_id,
                from_lens_id=from_lens,
                to_canonical_id=to_canonical_id,
                to_lens_id=to_lens,
                edge_type=edge_type,
            )
        except ValueError:
            await _audit_mutation_event(
                ctx,
                action=action,
                target_type="entity_allusion",
                target_id=evidence,
                outcome="denied",
                details={"reason": "invalid_edge", "edge_type": edge_type},
            )
            raise
        try:
            row, created = await allusion_store.propose_allusion(
                ctx.conn,
                from_canonical_id=from_canonical_id,
                from_lens_id=from_lens,
                to_canonical_id=to_canonical_id,
                to_lens_id=to_lens,
                edge_type=edge_type,
                method=_API_METHOD,
                confidence=confidence,
                rationale=rationale,
                evidence_document_id=evidence,
            )
        except psycopg.errors.InsufficientPrivilege as exc:
            await _audit_mutation_event(
                ctx,
                action=action,
                target_type="entity_allusion",
                target_id=evidence,
                outcome="denied",
                details={"reason": "evidence_not_writable"},
            )
            raise ValueError("not permitted to propose an allusion for that document") from exc
        if row is None:  # existing edge the caller may not read back (cross-lens)
            raise ValueError("that allusion already exists but is not visible to you")
        await _audit_mutation_event(
            ctx,
            action=action,
            target_type="entity_allusion",
            target_id=str(row["id"]),
            outcome="success",
            details={"edge_type": edge_type, "created": created},
        )
        return AllusionEdge.from_row(await allusion_read.hydrate_names(ctx.conn, row))

    @strawberry.mutation
    async def confirm_allusion(self, info: Info, id: strawberry.ID) -> AllusionEdge:
        """Confirm an allusion edge — a human verification (Block E, #9).

        Promotes a ``proposed`` edge to ``confirmed`` and records the reviewer. Confirming
        an allusion asserts the *relationship*, never an identity: it still does not merge
        the two entities. Requires write access to the edge's evidence document.
        """
        ctx = info.context
        await _require_write_audited(ctx, action="mutation.confirm_allusion")
        return await _apply_status(ctx, id, STATUS_CONFIRMED, "mutation.confirm_allusion")

    @strawberry.mutation
    async def reject_allusion(self, info: Info, id: strawberry.ID) -> AllusionEdge:
        """Reject an allusion edge (Block E, #9).

        Marks the edge ``rejected`` (records the reviewer); the substrate is untouched.
        Requires write access to the edge's evidence document.
        """
        ctx = info.context
        await _require_write_audited(ctx, action="mutation.reject_allusion")
        return await _apply_status(ctx, id, STATUS_REJECTED, "mutation.reject_allusion")
