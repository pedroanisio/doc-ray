"""Every GraphQL document the MCP tools send must validate against the API schema.

The MCP read/write tools embed hand-written GraphQL operation strings. This test is
the drift guard: it drives each tool with a fake transport, captures the exact
document it would send, and validates it against the live Strawberry schema
(fields, argument names, and types). If a resolver renames a field or changes an
argument, this fails here instead of at runtime against a 401/GraphQL error.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from graphql import build_schema, parse, validate

from doc_ray.entrypoints.api.schema import build_schema as build_strawberry_schema

# Import the server eagerly so the `mcp` SDK is loaded before any test patches
# httpx.AsyncClient — tokens._current_request() lazily imports it on first tool
# call, and the SDK evaluates `httpx.AsyncClient | None` at import time.
from doc_ray.entrypoints.mcp import corpus, mutations, semantic, server, structure  # noqa: F401

_GRAPHQL_SCHEMA = build_schema(build_strawberry_schema(debug=True).as_str())
_DOC = "11111111-1111-1111-1111-111111111111"
_MENTION = "01ARZ3NDEKTSV4RRFFQ69G5FAV"  # a ULID (entity_mention.id)


class _AutoData(dict):
    """A ``data`` payload that yields a benign value for whatever field a tool reads."""

    def __getitem__(self, key: str) -> Any:
        return []


class _CapturingClient:
    def __init__(self, sink: dict[str, str]) -> None:
        self._sink = sink

    async def __aenter__(self) -> _CapturingClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> Any:
        self._sink["query"] = json["query"]

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {"data": _AutoData()}

        return _Resp()


async def _capture(monkeypatch: pytest.MonkeyPatch, call) -> str:
    sink: dict[str, str] = {}
    monkeypatch.setattr(corpus.httpx, "AsyncClient", lambda **kw: _CapturingClient(sink))
    await call()
    return sink["query"]


# (tool callable) pairs covering every read tool that hits GraphQL and every write tool.
_READ_CALLS = [
    lambda: corpus.search_sentences("x"),
    lambda: corpus.full_text_search("x"),
    lambda: corpus.list_documents(),
    lambda: corpus.get_document(_DOC),
    lambda: corpus.read_document_sentences(_DOC),
    lambda: corpus.read_document_sentences(_DOC, from_ordinal=10, to_ordinal=20, page=3),
    lambda: corpus.read_document_sentences(_DOC, response_format="concise"),
    lambda: corpus.search_sentences("x", response_format="concise"),
    lambda: structure.read_document_outline(_DOC),
    lambda: structure.entity_profiles(),
    lambda: structure.entity_profiles(document_id=_DOC, entity_type="PERSON"),
    lambda: structure.term_frequency(["spice", "kwisatz"]),
    lambda: structure.entity_projection(_DOC),
    lambda: structure.entity_projection(_DOC, lens_id=_DOC, limit=10, after="c"),
    lambda: structure.lens_entities(_DOC),
    lambda: structure.lens_entities(_DOC, limit=10, after="c", min_mentions=2),
    lambda: structure.entity_relations(_DOC, 42),
    lambda: structure.entity_relations(_DOC, 42, limit=10, min_strength=0.1, after="c"),
    lambda: structure.related_entities(_DOC, 42),
    lambda: structure.related_entities(_DOC, 42, hops=3, limit=10),
    lambda: structure.allusions(42, _DOC),
    lambda: structure.allusions(42, _DOC, direction="outgoing", limit=10, after="c"),
    lambda: corpus.get_job("job-1"),
    lambda: corpus.list_collections(),
    lambda: corpus.get_collection(_DOC),
    lambda: corpus.list_lenses(),
    lambda: corpus.corpus_manifest(4),
    lambda: corpus.corpus_shard(0, 4),
    lambda: corpus.entity_frequency(),
    lambda: corpus.lexeme_kwic("lex-1"),
    lambda: semantic.semantic_search_sentences("photosynthesis"),
    lambda: semantic.semantic_search_sentences("photosynthesis", document_id=_DOC),
    lambda: corpus.entity_mention_corrections(_DOC),
    lambda: structure.document_classification(_DOC),
    lambda: corpus.document_images(_DOC),
    lambda: corpus.image_class_summary(_DOC),
    lambda: corpus.visual_captions(_DOC),
    lambda: corpus.visual_review(document_id=_DOC),
    lambda: corpus.cross_modal_image_search("dune"),
]

_WRITE_CALLS = [
    lambda: mutations.reannotate(_DOC),
    lambda: mutations.reextract_document(_DOC),
    lambda: mutations.annotate_document(_DOC),
    lambda: mutations.semantic_enrich_document(_DOC),
    lambda: mutations.embed_document_sentences(_DOC),
    lambda: mutations.canonicalize_document(_DOC, resolve=True),
    lambda: mutations.suggest_seeds(_DOC),
    lambda: mutations.suggest_document_metadata(_DOC),
    lambda: mutations.update_document_metadata(_DOC, title="t", tags=["a"]),
    lambda: mutations.bulk_tag_documents([_DOC], tags=["a"]),
    lambda: mutations.correct_entity_mention(
        _DOC, "reclassify", 16, 22, entity_type="GPE", original_type="PERSON"
    ),
    lambda: mutations.correct_entity_mention(
        _DOC, "adjust_span", 16, 22, new_char_start=16, new_char_end=26
    ),
    lambda: mutations.classify_document(_DOC),
    lambda: mutations.confirm_document_classification(_DOC, "fiction", subtype="novel"),
    lambda: mutations.confirm_projection(_MENTION, _DOC),
    lambda: mutations.reject_projection(_MENTION, _DOC),
    lambda: mutations.propose_allusion(42, _DOC, 84, _DOC, "alludes_to", _DOC),
    lambda: mutations.confirm_allusion(_DOC),
    lambda: mutations.reject_allusion(_DOC),
    lambda: mutations.decide_seeds(approve_ids=["s1"]),
    lambda: mutations.decide_document_metadata_suggestions(approve_ids=["m1"]),
    lambda: mutations.decide_visual_captions(approve_ids=["v1"]),
    lambda: mutations.seed_approved(_DOC),
    lambda: mutations.create_collection("Dune Series", tags=["dune"]),
    lambda: mutations.add_document_to_collection(_DOC, _DOC),
    lambda: mutations.remove_document_from_collection(_DOC, _DOC),
    lambda: mutations.update_document_metadata(_DOC, authors=["Frank Herbert"]),
    lambda: mutations.update_collection(_DOC, name="AI & Business"),
    lambda: mutations.delete_collection(_DOC),
    lambda: mutations.promote_collection_to_lens(_DOC),
    lambda: mutations.promote_collection_to_lens(_DOC, parent_lens_id=_DOC),
    lambda: mutations.demote_lens(_DOC),
    lambda: mutations.set_document_default_lens(_DOC, lens_id=_DOC),
    lambda: mutations.set_document_default_lens(_DOC),
]


@pytest.mark.parametrize("call", _READ_CALLS + _WRITE_CALLS)
@pytest.mark.asyncio
async def test_mcp_document_validates_against_the_api_schema(monkeypatch, call) -> None:
    document = await _capture(monkeypatch, call)
    errors = validate(_GRAPHQL_SCHEMA, parse(document))
    assert not errors, f"{document!r} -> {[str(e) for e in errors]}"


def test_api_schema_exposes_author_edit_and_collection_lifecycle() -> None:
    """The GraphQL surface the tools depend on: authors edit + collection update/delete."""
    from doc_ray.entrypoints.api.schema import build_schema as _bs

    sdl = _bs(debug=True).as_str()
    mutation = re.search(r"type Mutation \{(.*?)\n\}", sdl, re.S).group(1)
    assert "updateDocumentMetadata(" in mutation and "authors: [String!]" in mutation
    assert "updateCollection(collectionId: ID!" in mutation
    assert "deleteCollection(collectionId: ID!): Boolean!" in mutation


def test_api_schema_exposes_the_ner_correction_surface() -> None:
    """Block A: record a correction (mutation) and read the log back (query)."""
    from doc_ray.entrypoints.api.schema import build_schema as _bs

    sdl = _bs(debug=True).as_str()
    mutation = re.search(r"type Mutation \{(.*?)\n\}", sdl, re.S).group(1)
    query = re.search(r"type Query \{(.*?)\n\}", sdl, re.S).group(1)
    assert "correctEntityMention(" in mutation
    for arg in ("documentId: ID!", "action: String!", "charStart: Int!", "charEnd: Int!"):
        assert arg in mutation
    assert "entityMentionCorrections(documentId: ID!): [EntityMentionCorrection!]!" in query
    assert "type EntityMentionCorrection {" in sdl


def test_api_schema_exposes_the_lens_registry_surface() -> None:
    """Block B: promote/demote lenses, set a default, and read scopes back."""
    from doc_ray.entrypoints.api.schema import build_schema as _bs

    sdl = _bs(debug=True).as_str()
    mutation = re.search(r"type Mutation \{(.*?)\n\}", sdl, re.S).group(1)
    query = re.search(r"type Query \{(.*?)\n\}", sdl, re.S).group(1)
    dc = re.search(r"type DocumentCollection \{(.*?)\n\}", sdl, re.S).group(1)
    doc = re.search(r"type Document \{(.*?)\n\}", sdl, re.S).group(1)
    assert "promoteCollectionToLens(collectionId: ID!, parentLensId: ID" in mutation
    assert "demoteLens(collectionId: ID!): DocumentCollection!" in mutation
    assert "setDocumentDefaultLens(documentId: ID!, lensId: ID" in mutation
    assert "lenses(limit: Int! = 50, after: String" in query
    assert "isScope: Boolean!" in dc and "parentLensId: ID" in dc
    assert "defaultLensId: ID" in doc


def test_api_schema_exposes_corpus_sharding() -> None:
    """The GraphQL surface Block 2 depends on: corpusManifest + corpusShard."""
    from doc_ray.entrypoints.api.schema import build_schema as _bs

    sdl = _bs(debug=True).as_str()
    query = re.search(r"type Query \{(.*?)\n\}", sdl, re.S).group(1)
    assert "corpusManifest(shardCount: Int!): CorpusManifest!" in query
    assert "corpusShard(shardIndex: Int!, shardCount: Int!" in query
    assert query.count("corpusShard") == 1 and "): [Document!]!" in query
    assert "type CorpusManifest {" in sdl and "type CorpusShardInfo {" in sdl


def test_api_schema_exposes_ranged_sentence_read() -> None:
    """Block 3a: Document.sentences gains random-access range/page arguments."""
    from doc_ray.entrypoints.api.schema import build_schema as _bs

    sdl = _bs(debug=True).as_str()
    doc = re.search(r"type Document \{(.*?)\n\}", sdl, re.S).group(1)
    sentences = re.search(r"sentences\(([^)]*)\)", doc).group(1)
    assert "fromOrdinal: Int" in sentences
    assert "toOrdinal: Int" in sentences
    assert "page: Int" in sentences


@pytest.mark.asyncio
async def test_every_write_tool_is_contract_checked() -> None:
    """Guard against a new write tool being added without a schema-contract case."""
    covered = {
        "reannotate",
        "reextract_document",
        "annotate_document",
        "semantic_enrich_document",
        "embed_document_sentences",
        "canonicalize_document",
        "suggest_seeds",
        "suggest_document_metadata",
        "update_document_metadata",
        "bulk_tag_documents",
        "correct_entity_mention",
        "classify_document",
        "confirm_document_classification",
        "confirm_projection",
        "reject_projection",
        "propose_allusion",
        "confirm_allusion",
        "reject_allusion",
        "decide_seeds",
        "decide_document_metadata_suggestions",
        "decide_visual_captions",
        "seed_approved",
        "create_collection",
        "add_document_to_collection",
        "remove_document_from_collection",
        "update_collection",
        "delete_collection",
        "promote_collection_to_lens",
        "demote_lens",
        "set_document_default_lens",
    }
    assert {fn.__name__ for fn in mutations.TOOLS} == covered
