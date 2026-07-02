"""Stage 2 — candidate generation / blocking (deterministic, no LLM).

The flywheel entry point: normalize a mention's surface and look it up in the remissive
index. A single, high-confidence canonical hit is **auto-linked** and never reaches the
frontier model; an ambiguous (several distinct canonicals) or novel (no hit) mention
becomes residue for Stage 3. Embedding-similarity and Wikidata-search fallbacks (P3+) slot
in behind this index-first pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import psycopg

from doc_ray.adapters import canonical_store as store
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.ports.resolver import Candidate

_Conn = psycopg.Connection[dict[str, Any]]

#: A single index candidate at or above this confidence is auto-linked (skips the LLM).
AUTO_LINK_THRESHOLD = 0.9


@dataclass(frozen=True)
class Blocking:
    """Stage-2 result for one mention.

    Attributes:
        candidates: Distinct canonical candidates (best-confidence row per canonical).
        auto_link_id: The canonical id to auto-link, set iff exactly one candidate cleared
            :data:`AUTO_LINK_THRESHOLD`; ``None`` means ambiguous or novel (-> residue).
    """

    candidates: list[Candidate] = field(default_factory=list)
    auto_link_id: int | None = None


def candidates_for_form(conn: _Conn, surface: str) -> Blocking:
    """Shortlist canonical candidates for a surface form via the remissive index."""
    best: dict[int, dict[str, Any]] = {}
    for row in store.lookup_form(conn, form_norm(surface)):
        best.setdefault(int(row["canonical_id"]), row)  # rows are conf-desc, so first wins

    candidates = [
        Candidate(canonical_id=cid, preferred=row["preferred"], entity_type=row["entity_type"])
        for cid, row in best.items()
    ]

    auto_link_id: int | None = None
    if len(best) == 1:
        ((cid, row),) = best.items()
        if row["confidence"] >= AUTO_LINK_THRESHOLD:
            auto_link_id = cid
    return Blocking(candidates=candidates, auto_link_id=auto_link_id)
