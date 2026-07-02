"""Canonicalization orchestrator — run the pipeline over a document's mentions.

P2 scope: the deterministic Stage-2 pass. For each entity mention, block on the remissive
index and auto-link the confident ones (writing the mention -> canonical edge). Ambiguous /
novel mentions are returned as **residue** — the input to the frontier resolver (Stage 3)
and the review queue (Stage 5), wired in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg

from doc_ray.adapters import canonical_store as store
from doc_ray.application.candidate_generation import candidates_for_form
from doc_ray.ports.resolver import Candidate

_Conn = psycopg.Connection[dict[str, Any]]


@dataclass(frozen=True)
class ResidueMention:
    """A mention Stage 2 could not confidently resolve (goes to the frontier / review)."""

    mention_id: str
    surface: str
    entity_type: str
    candidates: list[Candidate] = field(default_factory=list)
    char_start: int = 0
    char_end: int = 0


@dataclass(frozen=True)
class AutolinkResult:
    """Outcome of the deterministic Stage-2 pass over one document."""

    linked: int
    residue: list[ResidueMention] = field(default_factory=list)


def autolink_document(conn: _Conn, document_id: str) -> AutolinkResult:
    """Auto-link every confidently-resolvable mention in a document; collect the residue."""
    linked = 0
    residue: list[ResidueMention] = []
    for mention in store.mentions_for_document(conn, document_id):
        surface = mention["surface"] or ""
        blocking = candidates_for_form(conn, surface)
        if blocking.auto_link_id is not None:
            store.link_mention(conn, str(mention["mention_id"]), blocking.auto_link_id)
            linked += 1
        else:
            residue.append(
                ResidueMention(
                    mention_id=str(mention["mention_id"]),
                    surface=surface,
                    entity_type=mention["entity_type"],
                    candidates=blocking.candidates,
                    char_start=int(mention["char_start"]),
                    char_end=int(mention["char_end"]),
                )
            )
    return AutolinkResult(linked=linked, residue=residue)
