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

from doc_ray.entrypoints.mcp.corpus import ToolCallError, _require_uuid, gql


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


async def term_frequency(terms: list[str], document_id: str | None = None) -> list[dict[str, Any]]:
    """Check how common terms are BEFORE spending a search — avoid flooding, don't just diagnose it.

    ``search_sentences`` already tells you *after the fact* when a term flooded one
    region (its ``coverage.limit_reached`` + note). This tells you *first*, for
    free: pass a batch of candidate terms and get a ``floodRisk`` verdict
    (``low`` | ``moderate`` | ``high``) per term, so you pick a rarer, scene-specific
    term instead of burning a call on a common one that clusters its hits and
    starves coverage of the rest of the document.

    Batch by default — triage all your candidates in ONE call
    (``term_frequency(terms=["spice","sandworm","kwisatz"])``); do not spend one
    round-trip per term. Each result carries the numbers behind the verdict —
    ``uses`` (corpus-wide count), ``totalUses`` (the scope total), ``share`` — so you
    can judge for yourself, plus ``attested`` (false + an "unattested" note means the
    term is absent from the lexicon, so a search will likely return nothing) and a
    steering ``note`` on the risky ones.

    For a whitespace-separated phrase, ``measurement`` and ``uses`` retain the
    rarest-component ``component_upper_bound`` as secondary lexeme evidence. The
    primary verdict comes from the direct sentence scan below; separate component
    counts alone still do not prove adjacency.

    **Multi-word phrases and compounds are measured, not declined.** The lexeme
    index tokenizes on underscores and digits, so it cannot hold ``Model Context
    Protocol``, ``shared_buffers`` or ``COVID-19`` as single entries. Rather than
    answering ``unknown``, SQL fetches a bounded candidate superset and the server
    verifies every occurrence under ``surface_word_v1``. It returns ``method:
    "sentence_scan"``, ``profile``, ``sentenceHits`` (candidate sentences),
    ``occurrenceSentences`` (verified sentences), ``occurrences`` (all overlapping
    verified starts), ``totalSentences``, and ``floodRisk``. The verdict and
    ``attested`` use the verified sentence count, so word-internal false positives
    do not inflate either. Batch candidates: they cost one statement together.

    ``capped: true`` means candidate fetching stopped at the server's cap, so
    ``sentenceHits``, ``occurrenceSentences``, and ``occurrences`` are all FLOORS —
    unseen candidates may add matches. Treat the result as "floods", never as exact.
    If none of the fetched candidates verifies, ``attested`` is null rather than
    a false absence claim. ``parts`` still carries the rarest-component upper bound as
    secondary evidence. A single word keeps ``method: "lexeme_lemma"`` and its exact
    lexeme numbers, unchanged.

    If ``sentenceHits`` is positive but the maintained corpus denominator is stale
    or missing, ``totalSentences`` is ``null`` and ``note`` says the denominator is
    unavailable. The absolute hit-band ``floodRisk`` remains usable; never rewrite
    the result as "N of 0 sentences". A real empty visible corpus can still return
    zero hits and ``totalSentences: 0``.

    The count is the lexeme-lemma frequency (matched with the lexeme layer's
    normalization). It is a corpus-saturation signal, a strong proxy for — not an
    exact predictor of — any one search tool's hit count. Corpus-wide only for now:
    pass ``document_id`` and it fails with a typed ``unsupported`` error rather than
    return corpus numbers mislabelled as document-scoped.

    Returns a list of ``{term, normalizedTerm, uses, totalUses, share, floodRisk,
    attested, indexable, measurement, parts, note, method, profile, sentenceHits,
    occurrenceSentences, occurrences, totalSentences, capped}``, one per distinct
    term, in input order.
    """
    if document_id is not None:
        _require_uuid(document_id, "document_id")
        raise ToolCallError(
            "unsupported",
            "document-scoped term frequency is not available yet — this reads the "
            "corpus-wide lexeme_stats rollup.",
            hint=(
                "omit document_id for corpus-wide frequency; to navigate within one "
                "document use read_document_outline + read_document_sentences"
            ),
        )
    if not terms:
        raise ToolCallError(
            "bad_input",
            "terms must contain at least one term",
            hint='pass the candidate search terms you are triaging, e.g. ["spice","sandworm"]',
        )
    data = await gql(
        "query($t:[String!]!){termFrequency(terms:$t)"
        "{term normalizedTerm uses totalUses share floodRisk attested indexable measurement "
        "parts{part uses} note method profile sentenceHits occurrenceSentences occurrences "
        "totalSentences capped}}",
        {"t": terms},
    )
    return list(data["termFrequency"])


async def document_classification(document_id: str) -> dict[str, Any] | None:
    """A document's genre classification, or null if unclassified (SLP Block C).

    Returns the genre axis (``fiction`` / ``non_fiction`` / ``mixed`` / ``unknown``)
    that separates works whose entities are local to the book from real-world
    references. ``status`` is the trust signal: ``pending`` is an UNVERIFIED machine
    GUESS — do NOT rely on it for identity/merge decisions — while ``confirmed``
    (``method='human'``) is a human decision. ``confidence`` (0–1) accompanies a
    machine guess. Enqueue a fresh guess with ``classify_document``; make it
    authoritative with ``confirm_document_classification``. RLS-scoped to documents
    your token may read; null for an unclassified document.
    """
    _require_uuid(document_id, "document_id")
    data = await gql(
        "query($d:ID!){documentClassification(documentId:$d)"
        "{documentId genre subtype confidence method status reviewer rationale "
        "createdAt updatedAt}}",
        {"d": document_id},
    )
    row: dict[str, Any] | None = data["documentClassification"]
    return row


async def entity_projection(
    document_id: str, lens_id: str | None = None, limit: int = 100, after: str | None = None
) -> dict[str, Any]:
    """A document's entity identity through a lens — ``project(mention, lens)`` (SLP Block D).

    Answers "which entity is each mention, *within this lens*". ``scope`` is ``local``
    (an identity local to a fictional work) or ``global`` (the shared real-world
    registry); ``canonicalId`` is null when a mention is explicitly unresolved (it is
    never silently dropped). ``status`` is the trust signal: ``proposed`` is an
    UNVERIFIED machine guess — confirm it with ``confirm_projection`` before relying on
    it (PALS's Law). Omit ``lensId`` to use the document's default lens, else the
    ``global`` lens; ``ambiguous`` is true when it fell back to ``global`` because the
    document has several projecting lenses and no default. Page with ``entities[-1].cursor``
    as ``after``. RLS-scoped to documents your token may read.
    """
    _require_uuid(document_id, "document_id")
    if lens_id is not None:
        _require_uuid(lens_id, "lens_id")
    data = await gql(
        "query($d:ID!,$l:ID,$n:Int!,$a:String){entityProjection"
        "(documentId:$d,lensId:$l,limit:$n,after:$a){lensId isDefault ambiguous nextCursor "
        "entities{mentionId lensId documentId canonicalId scope method confidence status "
        "surface mentionType charStart charEnd preferred canonicalType wikidataId cursor}}}",
        {"d": document_id, "l": lens_id, "n": limit, "a": after},
    )
    result: dict[str, Any] = data["entityProjection"]
    return result


async def lens_entities(
    lens_id: str, limit: int = 100, after: str | None = None, min_mentions: int = 0
) -> list[dict[str, Any]]:
    """The significant entities in a lens, most-mentioned first (SLP Block F hyper-context).

    Prefer this over ``entity_frequency`` for "who and what is this lens about". Each entry
    has the entity's ``preferred`` name, ``scope`` (``local``/``global``), lens-wide
    ``nMentions`` / ``nDocuments``, its ``typeProfile`` + ``typeConsistency`` (low = the
    annotator disagreed with itself; do not over-trust the type), ``firstSeenOrdinal`` (for
    citation), and ``refreshedAt``. **This is a precomputed rollup with a ``refreshedAt``
    staleness stamp**, not a live count. ``minMentions`` filters low-volume entities. Scope
    lenses only (for the corpus-wide real-world distribution use ``entity_profiles``). Page
    with a row's ``cursor``. RLS-scoped to lenses whose documents your token may read.
    """
    _require_uuid(lens_id, "lens_id")
    data = await gql(
        "query($l:ID!,$n:Int!,$a:String,$m:Int!){lensEntities(lensId:$l,limit:$n,after:$a,"
        "minMentions:$m){canonicalId preferred entityType wikidataId scope nMentions nDocuments "
        "typeProfile typeConsistency firstSeenOrdinal refreshedAt cursor}}",
        {"l": lens_id, "n": limit, "a": after, "m": min_mentions},
    )
    rows: list[dict[str, Any]] = data["lensEntities"]
    return rows


async def entity_relations(
    lens_id: str,
    canonical_id: int,
    limit: int = 50,
    min_strength: float = 0.0,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """An entity's direct co-occurrence relationships within a lens, strongest first (Block F).

    Each entry is the *other* entity, with how many sentences they share
    (``cooccurrenceCount``) and a ``strength`` — the **NPMI co-occurrence measure in
    [-1, 1], NOT a claim that the two entities are truly related** (structural association
    only; PALS's Law). ``minStrength`` filters weak associations. Precomputed rollup with a
    ``refreshedAt`` staleness stamp. Page with a row's ``cursor``. RLS-scoped to the lens.
    """
    _require_uuid(lens_id, "lens_id")
    data = await gql(
        "query($l:ID!,$c:Int!,$n:Int!,$a:String,$s:Float!){entityRelations(lensId:$l,"
        "canonicalId:$c,limit:$n,after:$a,minStrength:$s){canonicalId preferred entityType "
        "wikidataId cooccurrenceCount strength refreshedAt cursor}}",
        {"l": lens_id, "c": canonical_id, "n": limit, "a": after, "s": min_strength},
    )
    rows: list[dict[str, Any]] = data["entityRelations"]
    return rows


async def related_entities(
    lens_id: str, canonical_id: int, hops: int = 2, limit: int = 50
) -> list[dict[str, Any]]:
    """Discover entities within N co-occurrence hops of an anchor in a lens (Block F).

    Bounded breadth-first over the lens's co-occurrence graph; each entry carries its
    shortest ``hops`` distance. ``hops`` is **capped at 3 and a larger value is rejected**
    (never silently clamped) — there is no unbounded graph walk. Reachability is a
    **structural** signal, not an assertion of a real relationship (PALS's Law). Use it for
    "friend-of-a-friend" discovery; the direct relations are ``entity_relations``.
    """
    _require_uuid(lens_id, "lens_id")
    data = await gql(
        "query($l:ID!,$c:Int!,$h:Int!,$n:Int!){relatedEntities(lensId:$l,canonicalId:$c,"
        "hops:$h,limit:$n){canonicalId preferred entityType wikidataId hops}}",
        {"l": lens_id, "c": canonical_id, "h": hops, "n": limit},
    )
    rows: list[dict[str, Any]] = data["relatedEntities"]
    return rows


async def allusions(
    canonical_id: int,
    lens_id: str,
    direction: str = "both",
    limit: int = 50,
    after: str | None = None,
) -> list[dict[str, Any]]:
    """Cross-lens allusion edges for an entity — a typed link, NOT an identity (SLP Block E).

    ⚠ An allusion is an EDGE, never an equality. It records that one entity *alludes to /
    depicts / references / is adapted from / inspired by* another (e.g. a novel's Paris →
    the real Paris) WITHOUT merging them. **Never treat a returned edge as identity**, and
    **never chain edges transitively**: ``A→B`` and ``B→C`` does NOT mean ``A→C`` or
    ``A≡C``. ``direction`` = ``outgoing`` (what this entity alludes to), ``incoming``
    (what alludes to it), or ``both``. ``status`` tells you whether an edge is a
    ``proposed`` guess or a ``confirmed`` human decision. Only edges whose both lenses
    (and evidence document) your token may see are returned. Page with a row's ``cursor``.
    """
    _require_uuid(lens_id, "lens_id")
    data = await gql(
        "query($c:Int!,$l:ID!,$d:String!,$n:Int!,$a:String){allusions"
        "(canonicalId:$c,lensId:$l,direction:$d,limit:$n,after:$a){id edgeType status "
        "confidence method fromCanonicalId fromLensId toCanonicalId toLensId "
        "fromPreferred toPreferred fromLensName toLensName evidenceDocumentId rationale "
        "createdAt cursor}}",
        {"c": canonical_id, "l": lens_id, "d": direction, "n": limit, "a": after},
    )
    rows: list[dict[str, Any]] = data["allusions"]
    return rows


#: Registered by ``server.build_server`` next to the corpus read tools.
TOOLS: tuple[Callable[..., Any], ...] = (
    read_document_outline,
    entity_profiles,
    term_frequency,
    document_classification,
    entity_projection,
    lens_entities,
    entity_relations,
    related_entities,
    allusions,
)
