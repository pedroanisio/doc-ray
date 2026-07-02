"""Stage 0 — seed the canonical registry from a knowledge base.

Given Wikidata ids (or search terms), pull each entity's labels/aliases and write a
``canonical`` row plus its remissive-index forms, so the registry has a canonical spine
*before* any messy source is resolved. Idempotent per ``wikidata_id``. The
:class:`KnowledgeBase` is injected, so this is unit-testable with a stub (no network).
"""

from __future__ import annotations

from typing import Any

import psycopg

from doc_ray.adapters import canonical_store as store
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.ports.knowledge_base import KbEntry, KnowledgeBase

_Conn = psycopg.Connection[dict[str, Any]]


def seed_entry(conn: _Conn, entry: KbEntry, *, source_type: str = "wikipedia") -> int:
    """Create/reuse a canonical row for *entry* and register its forms; return its id."""
    canonical_id = store.create_canonical(
        conn,
        preferred=entry.label,
        entity_type=entry.entity_type or "UNKNOWN",
        wikidata_id=entry.kb_id or None,
        description=entry.description,
        lang_primary=entry.lang,
    )
    store.upsert_form(
        conn,
        form_norm=form_norm(entry.label),
        form_surface=entry.label,
        canonical_id=canonical_id,
        lang=entry.lang or "",
        form_type="name",
        source_type=source_type,
    )
    for alias in entry.aliases:
        if not alias.strip():
            continue
        store.upsert_form(
            conn,
            form_norm=form_norm(alias),
            form_surface=alias,
            canonical_id=canonical_id,
            form_type="alias",
            source_type=source_type,
        )
    return canonical_id


def seed_from_ids(
    conn: _Conn, kb: KnowledgeBase, kb_ids: list[str], *, source_type: str = "wikipedia"
) -> list[int]:
    """Seed canonical entities for each known KB id; return the canonical ids created/reused."""
    out: list[int] = []
    for kb_id in kb_ids:
        entry = kb.get(kb_id)
        if entry is not None:
            out.append(seed_entry(conn, entry, source_type=source_type))
    return out


def seed_from_terms(
    conn: _Conn, kb: KnowledgeBase, terms: list[str], *, source_type: str = "wikipedia"
) -> list[int]:
    """Seed the top KB hit for each search term; return the canonical ids created/reused."""
    out: list[int] = []
    for term in terms:
        hits = kb.search(term)
        if not hits:
            continue
        entry = kb.get(hits[0].kb_id) or hits[0]
        out.append(seed_entry(conn, entry, source_type=source_type))
    return out
