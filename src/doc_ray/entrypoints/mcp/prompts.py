"""MCP prompts primitive — the corpus methodology as user-selectable workflows (Item 8 / #13).

MCP defines three primitives by *who controls them*: tools are model-controlled,
resources are application-controlled, and **prompts are user-controlled**. doc-ray
already ships tools (``corpus``/``structure``/``compose``) and resources
(``resources.py``); this module adds the missing third primitive.

Each prompt returns a single user-role message (a ``str``; FastMCP wraps it as a
``UserMessage``) carrying a workflow this corpus is actually used for. The lesson
lives in the server, not the user's prompt — the same thesis as ``SERVER_INSTRUCTIONS``
(BL-145), but for the primitive a *user* invokes deliberately, with arguments.

``summarize_document`` is **eager** (Open decision 2): it calls the real read tools
to parameterize its plan with the document's actual title, sentence count, and
outline ranges (FR-2) — a static prompt would be a worse ``SERVER_INSTRUCTIONS`` and
would not justify the primitive. Honesty propagates (FR-3): a ``uniform`` outline is
labelled as a navigation grid, never laundered into a table of contents. The other
three are argument-parameterized methodology plans.

No database access: prompts call the tool functions, inheriting the caller's
forwarded token and RLS. They never embed a credential in the returned text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from doc_ray.entrypoints.mcp import corpus, structure
from doc_ray.entrypoints.mcp.corpus import ToolCallError, _require_uuid

#: Upper bound on shards a single sweep will plan — matches corpus_manifest's sanity.
_MAX_SHARDS = 64


def _coverage_lines(entries: list[dict[str, object]]) -> str:
    """Render outline entries as an ordered, ordinal-addressed read plan."""
    lines = []
    for entry in entries:
        title = str(entry.get("title") or "").strip().replace("\n", " ")
        start, end = entry.get("ordinalStart"), entry.get("ordinalEnd")
        lines.append(f"  - {title}: ordinals {start}–{end}")
    return "\n".join(lines)


def _degraded_summary(title: str, sentence_count: int, document_id: str) -> str:
    """A summary plan for when the outline could not be fetched (Open decision 2)."""
    return (
        f'Summarize "{title}" ({sentence_count} sentences), working from retrieval, '
        "not memory: every claim in your summary must carry an (ordinal) citation.\n\n"
        "NOTE: I could not fetch this document's outline just now, so no ranges are "
        "pre-filled. Call read_document_outline(document_id) yourself first to get its "
        "addressable ordinal ranges (check its `source`: 'headings' is real structure, "
        "'uniform' is an even grid whose titles are NOT chapter names). Then read each "
        'range with read_document_sentences(from_ordinal, to_ordinal, response_format="concise") '
        "until you have covered all sentences.\n\n"
        "Deliver a tight summary where every assertion cites its ordinal; report "
        "anything you could not find as a gap rather than inventing it."
    )


async def summarize_document(document_id: str) -> str:
    """Plan an exhaustive, citation-backed summary of one document (outline-then-ranged-read).

    Eagerly fetches the document's real title, sentence count, and outline so the
    returned plan already names the ordinal ranges to read — instead of the model
    re-deriving the methodology. Honest about a heading-less document: a uniform
    grid is labelled as such, never presented as a table of contents.
    """
    _require_uuid(document_id, "document_id")
    doc = await corpus.get_document(document_id)
    if not doc:
        return (
            "That document id is not readable with your token (unknown, or outside "
            "your visibility). Call list_documents to find a document id you can read, "
            "then request this prompt again. No summary plan was produced."
        )
    title = str(doc.get("title") or "(untitled)")
    sentence_count = int(doc.get("sentenceCount") or 0)

    try:
        outline = await structure.read_document_outline(document_id)
    except ToolCallError:
        return _degraded_summary(title, sentence_count, document_id)
    entries = outline.get("entries") or []
    source = str(outline.get("source") or "empty")
    if not entries:
        return _degraded_summary(title, sentence_count, document_id)
    total = int(outline.get("totalSentences") or sentence_count)

    if source == "uniform":
        provenance = (
            "This is a `uniform` outline: no headings were detected, so the ranges "
            "below are an even split of the sentences. The entry titles are the "
            "sentence each block opens on — they are NOT chapter names, so do not quote "
            "them as such."
        )
    else:
        provenance = (
            "This is a `headings` outline built from detected structure (heuristic, so "
            "it may be coarse). The titles are real headings you may cite."
        )

    return (
        f'Summarize "{title}" ({total} sentences). Work from retrieval, not memory: '
        "every claim in your summary must carry an (ordinal) citation back to the text.\n\n"
        "Coverage plan — read these ranges in order with "
        'read_document_sentences(from_ordinal, to_ordinal, response_format="concise"):\n'
        f"{_coverage_lines(entries)}\n\n"
        f"{provenance}\n\n"
        "For each range: read it concisely, capture the beats with their ordinals, then "
        "move on. Covering every range covers the whole document. If you need a specific "
        "detail, prefer a rare, scene-specific search term (term_frequency can pre-check "
        "a term's flood risk). Deliver a tight summary in which every assertion cites its "
        "ordinal, and flag anything you could not find rather than inventing it."
    )


async def ground_a_claim(claim: str, document_id: str | None = None) -> str:
    """Plan how to ground a specific claim in cited corpus evidence (search-then-verify).

    Returns the retrieve-then-quote methodology parameterized by the claim: find the
    supporting sentences, read their surrounding range, quote verbatim with offsets,
    and — the non-negotiable rule — report an unsupported claim as unsupported rather
    than asserting it from memory.
    """
    _require_uuid(document_id, "document_id")
    scope = f" Scope every search to document {document_id}." if document_id else ""
    return (
        f'Ground this claim in the corpus before asserting it:\n\n  "{claim}"\n\n'
        "Method — search, then verify, never assert from memory:\n"
        "1. Pick the rarest, most scene-specific term in the claim and call "
        "search_sentences(term). Common words flood one region and starve the rest; "
        "term_frequency can pre-check a term's flood risk before you spend the call.\n"
        "2. For each promising hit, read the surrounding range with "
        "read_document_sentences(from_ordinal, to_ordinal) — context, not just the matched line.\n"
        "3. Quote the supporting text verbatim, with its (ordinal + charStart/charEnd) offsets.\n\n"
        "Rule (non-negotiable): a claim you cannot tie to a retrieved sentence is reported "
        "as UNSUPPORTED, never asserted. Absence of evidence is itself a finding." + scope
    )


async def survey_entities(document_id: str | None = None) -> str:
    """Plan a who's-who of a document or the corpus using reconciled entity profiles.

    Returns the methodology for reading the cast honestly: prefer entity_profiles over
    raw entity_frequency, interpret typeConsistency as a measured (not model) score,
    corroborate a contested entity before asserting its type, and treat an unresolved
    entity as reconciled NER rather than a verified identity.
    """
    _require_uuid(document_id, "document_id")
    scope_arg = f"document_id={document_id}" if document_id else "no document_id (corpus-wide)"
    subject = "this document" if document_id else "the corpus"
    return (
        f"Survey who and what {subject} is about:\n"
        f"1. Call entity_profiles({scope_arg}) — NOT entity_frequency. Raw NER splits one "
        "entity across types and spellings; entity_profiles merges them into one row per "
        "entity with the full typeSpread and surfaceForms.\n"
        "2. Read typeConsistency: the dominant type's measured share of the mentions "
        "(arithmetic over NER output, not a model confidence). 1.0 means the annotator "
        "never disagreed; a low value (e.g. Arrakis at 0.41) means the type is CONTESTED.\n"
        "3. For any contested entity, corroborate with search_sentences before asserting "
        "what it is — do not report a contested type as settled.\n"
        "4. resolved:false means no canonical-registry backing: the row is reconciled NER "
        "and nothing more, so do not present it as a verified real-world identity.\n\n"
        "Deliver the roster ranked by mentions, each with its dominant type, whether that "
        "type is contested, and whether it is registry-resolved."
    )


async def sweep_the_corpus(shard_count: int = 8) -> str:
    """Plan an exhaustive map-reduce over the whole corpus, with a coverage proof.

    Returns the shard-then-page methodology over corpus_manifest / corpus_shard,
    including the arithmetic check that guarantees nothing was silently skipped:
    the per-shard document counts must sum to the manifest's totalDocuments.
    """
    if not isinstance(shard_count, int) or shard_count < 1 or shard_count > _MAX_SHARDS:
        raise ToolCallError(
            "bad_input",
            f"shard_count must be an integer in [1, {_MAX_SHARDS}], got {shard_count!r}",
            hint="8 is a sensible default for a corpus-wide sweep",
        )
    return (
        f"Sweep the whole corpus in {shard_count} disjoint passes (map-reduce), so nothing "
        "is silently skipped:\n"
        f"1. Call corpus_manifest(shard_count={shard_count}) ONCE to get the shard map: the "
        "per-shard document counts and the total.\n"
        f"2. For each shard i in 0..{shard_count - 1}, page "
        f"corpus_shard(shard_index=i, shard_count={shard_count}) — follow the returned cursor, "
        "stop on [] — and do your per-document work within that shard.\n"
        "3. Coverage proof: the shards are disjoint and exhaustive, so the sum of the "
        "documents you processed across all shards MUST equal the manifest's "
        "totalDocuments. If it does not, you dropped a shard — do not report a corpus-wide "
        "conclusion until it balances.\n\n"
        f"Use shard_count={shard_count} for the manifest AND every shard call; mixing "
        "different values re-partitions the corpus and breaks the coverage proof."
    )


#: Registered by ``server.build_server`` as MCP's user-controlled primitive.
PROMPTS: tuple[Callable[..., Awaitable[str]], ...] = (
    summarize_document,
    ground_a_claim,
    survey_entities,
    sweep_the_corpus,
)
