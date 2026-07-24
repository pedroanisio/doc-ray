"""MCP structure tools — the document outline and reconciled entity profiles (BL-146).

Split from :mod:`~doc_ray.entrypoints.mcp.corpus` (D-015) and registered
alongside it, so the read surface is one flat list of tools to an MCP client.
Both delegate through :func:`corpus.gql`, inheriting the same token forwarding,
typed errors, and RLS scoping as every other read tool.

These two exist to make the rest of the corpus *reachable*: the outline supplies
the ordinals the ranged reader needs, and the profiles collapse the raw NER
type-split into one row per entity with the disagreement measured rather than
hidden.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from doc_ray.entrypoints.mcp.corpus import _require_uuid, gql


async def read_document_outline(document_id: str, max_entries: int = 50) -> dict[str, Any]:
    """Map a document into addressable ordinal ranges — call this BEFORE reading it.

    ``read_document_sentences`` can jump to any slice, but only if you know which
    ordinals to ask for. This supplies them: a short list of regions, each with
    ``ordinalStart``/``ordinalEnd`` you pass straight back as
    ``from_ordinal``/``to_ordinal`` (and ``pageStart`` for the ``page`` reader).
    "Read the ending" becomes the last entry's range instead of a guess.

    Read ``source`` before trusting the titles. ``headings`` means real detected
    structure — though detection is heuristic (markup for EPUB/DOCX/MD, relative
    font size for PDFs), so some documents map coarsely. ``uniform`` means none
    was found and this is an even split of the sentences: a navigation grid,
    still exactly as good for aiming a ranged read, but NOT a table of contents —
    do not quote its titles as chapter names. ``empty`` means nothing to map.

    Returns ``{source, totalSentences, entryCount, note, entries}``.
    """
    _require_uuid(document_id, "document_id")
    data = await gql(
        "query($id:ID!,$m:Int!){documentOutline(documentId:$id,maxEntries:$m)"
        "{source totalSentences entryCount note "
        "entries{level title ordinalStart ordinalEnd charStart charEnd pageStart pageEnd}}}",
        {"id": document_id, "m": max_entries},
    )
    outline = data.get("documentOutline")
    if not outline:
        return {
            "source": "empty",
            "totalSentences": 0,
            "entryCount": 0,
            "note": "No such document, or it is not readable with this token.",
            "entries": [],
        }
    return dict(outline)


async def entity_profiles(
    document_id: str | None = None, entity_type: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Entity frequencies reconciled to ONE row per entity — prefer this to entity_frequency.

    Raw NER splits a single entity across types and spellings: in this corpus
    ``Arrakis`` is tagged LOC 225×, GPE 150×, PERSON 95×, and NORP 76×. This
    returns one row carrying the whole group — ``mentions`` (the real total),
    ``surfaceForms`` (the spellings folded in), ``typeSpread`` (every type with
    its count), and ``dominantType``.

    ``typeConsistency`` is the dominant type's measured share of the mentions,
    not a model score: 1.0 means the annotator never contradicted itself, and
    ``Arrakis`` scores 0.41 — read that as "this type is contested", and confirm
    with ``search_sentences`` before asserting what the entity is. ``resolved``
    says whether the entity is backed by the canonical registry; ``false`` means
    the row is reconciled NER and nothing more.

    Only normalization-equivalent spellings fold together — ``Duncan`` and
    ``Duncan Idaho`` stay separate rows, because relating them is coreference and
    guessing it would be fabrication.
    """
    _require_uuid(document_id, "document_id")
    data = await gql(
        "query($d:ID,$e:String,$l:Int!){entityProfiles(documentId:$d,entityType:$e,limit:$l)"
        "{surface normalizedForm mentions dominantType typeSpread{entityType count} "
        "typeConsistency surfaceForms canonicalId canonicalName wikidataId resolved}}",
        {"d": document_id, "e": entity_type, "l": limit},
    )
    profiles: list[dict[str, Any]] = data["entityProfiles"]
    return profiles


#: Registered by ``server.build_server`` next to the corpus read tools.
TOOLS: tuple[Callable[..., Any], ...] = (read_document_outline, entity_profiles)
