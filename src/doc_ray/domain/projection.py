"""The pure projection resolver — ``project(mention, lens)`` (SLP Block D, issue #8).

This is the heart of the Substrate·Lens·Projection architecture
(``docs/proposals/entity-fabric-substrate-lens-projection.md``, §3.3). Identity is
not a property baked into the substrate; it is a **derived interpretation**: the
mention is the immutable fact, and *who it is* depends on the lens you read it
through. The same corpus can then hold fiction and non-fiction side by side and
give each the identity rule it needs, without ever destroying the underlying facts.

This module is pure: stdlib only, no I/O, no psycopg, fully deterministic. It
*decides* a projection; it never mints identities or touches a database — the
application layer (``doc_ray.application.projection``) supplies the candidate maps
and persists what this returns. Keeping the decision here, unit-tested
exhaustively, is what makes the whole block safe: a wrong guess is derived and
rebuildable, never a load-bearing mutation of the substrate.

The resolution law (issue §5, FR-3), encoded once, below:

    policy decides local vs global → local-first lookup → global fallback →
    explicit unresolved.

⚠ ARCHITECTURAL CONTRACT (PALS's LAW) — a projection is a PROPOSAL, never a fact.
Every projection this module returns carries ``status='proposed'``: an exact-form
or model-assisted link is a candidate for human review, not an autonomous durable
identity claim (invariant 6). Only a human confirms.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from doc_ray.domain.genre_policy import Genre, ResolutionScope, resolution_scope

#: Resolution methods recorded on a projection (``entity_projection.method``).
METHOD_EXACT_FORM = "exact_form"  # deterministic surface-form match (local or global)
METHOD_UNRESOLVED = "unresolved"  # no candidate anywhere — explicit, not a silent drop

#: The only status the machine emits. Human confirmation writes 'confirmed'/'rejected'.
STATUS_PROPOSED = "proposed"

#: Projection scopes — single-sourced against the genre policy so the DB CHECK
#: (``scope IN ('local','global')``) and this module cannot drift.
SCOPE_LOCAL = ResolutionScope.LOCAL.value  # 'local'
SCOPE_GLOBAL = ResolutionScope.GLOBAL.value  # 'global'


@dataclass(frozen=True)
class Mention:
    """One immutable NER fact from the substrate — the input to a projection."""

    mention_id: str
    entity_type: str
    surface: str


@dataclass(frozen=True)
class Lens:
    """The scope through which a mention is interpreted.

    ``is_global`` marks the reserved ``global`` lens (migration 121), the shared
    real-world identity space: it forces GLOBAL resolution for every mention
    regardless of genre, so a fictional surface read through ``global`` resolves to
    its real-world referent (or unresolved), never to a work-local identity.
    """

    lens_id: str
    is_global: bool


@dataclass(frozen=True)
class GlobalLink:
    """A mention's pre-existing global resolution (``entity_link`` → ``canonical``)."""

    canonical_id: int
    confidence: float | None


@dataclass(frozen=True)
class Projection:
    """The derived interpretation of a mention within a lens — one projection row.

    ``canonical_id is None`` means **explicitly unresolved**: the mention matched
    no local or global identity and was not dropped (invariant 4).
    """

    mention_id: str
    lens_id: str
    canonical_id: int | None
    scope: str  # SCOPE_LOCAL | SCOPE_GLOBAL
    method: str
    confidence: float | None
    status: str


def local_key(surface: str, entity_type: str) -> tuple[str, str]:
    """The stable key a lens-local identity is minted and looked up under.

    Case- and whitespace-insensitive on the surface (so ``"  Duncan "`` and
    ``"duncan"`` are one identity) and upper-cased on the type (so a ``person`` row
    and a ``PERSON`` row key identically) — matching how Block C's
    :func:`resolution_scope` compares types.
    """
    return (" ".join(surface.split()).casefold(), entity_type.strip().upper())


def project(
    mention: Mention,
    lens: Lens,
    genre: Genre,
    *,
    local_candidates: Mapping[tuple[str, str], int],
    global_index: Mapping[str, GlobalLink],
) -> Projection:
    """Return the projection of *mention* within *lens* under the document's *genre*.

    Pure and deterministic — identical inputs always yield an identical
    :class:`Projection` (reproducibility, invariant 3). The resolution law:

    * **Lens rule.** The reserved ``global`` lens forces ``GLOBAL``; a scope lens
      applies the Block C genre policy (:func:`resolution_scope`) per entity type.
    * **LOCAL** (fiction-local type in a scope lens): resolve to the lens-local
      identity minted for the mention's ``(surface, type)``; on a miss, **fall back
      to GLOBAL** — the global lens is never bypassed (invariant 4).
    * **GLOBAL**: resolve via the mention's pre-existing global link; on a miss,
      remain **explicitly unresolved** (``canonical_id=None``) — never dropped.

    Args:
        mention: The substrate fact being interpreted.
        lens: The scope of interpretation.
        genre: The document's (untrusted) genre label — routes local vs global.
        local_candidates: ``{local_key(surface, type): canonical_id}`` for the
            identities minted in *lens*. The application layer pre-mints these.
        global_index: ``{mention_id: GlobalLink}`` — the current global resolution.

    Returns:
        A :class:`Projection` with ``status='proposed'`` (PALS's Law).
    """
    if lens.is_global:
        decision = ResolutionScope.GLOBAL
    else:
        decision = resolution_scope(genre, mention.entity_type)

    if decision is ResolutionScope.LOCAL:
        cid = local_candidates.get(local_key(mention.surface, mention.entity_type))
        if cid is not None:
            return Projection(
                mention_id=mention.mention_id,
                lens_id=lens.lens_id,
                canonical_id=cid,
                scope=SCOPE_LOCAL,
                method=METHOD_EXACT_FORM,
                confidence=1.0,
                status=STATUS_PROPOSED,
            )
        # local miss → global fallback (invariant 4: global is never bypassed).

    link = global_index.get(mention.mention_id)
    if link is not None:
        return Projection(
            mention_id=mention.mention_id,
            lens_id=lens.lens_id,
            canonical_id=link.canonical_id,
            scope=SCOPE_GLOBAL,
            method=METHOD_EXACT_FORM,
            confidence=link.confidence,
            status=STATUS_PROPOSED,
        )
    return Projection(
        mention_id=mention.mention_id,
        lens_id=lens.lens_id,
        canonical_id=None,
        scope=SCOPE_GLOBAL,
        method=METHOD_UNRESOLVED,
        confidence=None,
        status=STATUS_PROPOSED,
    )
