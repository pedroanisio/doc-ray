"""Regression guard for the NLP capability matrix (BL-016).

The database schema can support more NLP layers than the API/browser currently
expose. This guard keeps the capability matrix honest by checking that rows
marked writer-backed or API-exposed cite live code paths instead of drifting
into aspirational prose.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MATRIX = _ROOT / "docs" / "nlp_capability_matrix.md"
_QUERY = _ROOT / "src" / "doc_ray" / "entrypoints" / "api" / "query.py"
#: `Query` resolvers also live in mixins inherited by the root (D-015 split):
#: e.g. semantic retrieval in semantic_query.py. Query field refs resolve against
#: the root plus its mixins.
_QUERY_MIXINS = (
    _ROOT / "src" / "doc_ray" / "entrypoints" / "api" / "semantic_query.py",
    _ROOT / "src" / "doc_ray" / "entrypoints" / "api" / "sentence_search_query.py",
)
_TYPES = _ROOT / "src" / "doc_ray" / "entrypoints" / "api" / "types.py"
#: The document cluster's types moved to document_types.py (D-015 split);
#: field refs on `Document` resolve against both files.
_DOCUMENT_TYPES = _ROOT / "src" / "doc_ray" / "entrypoints" / "api" / "document_types.py"
_BACKLOG = _ROOT / "docs" / "backlog.yml"

_REF_RE = re.compile(r"`([^`]+)`")
_GRAPHQL_REF_RE = re.compile(r"`(Query|Document|Lexeme)\.([A-Za-z_][A-Za-z0-9_]*)`")


def _table() -> list[dict[str, str]]:
    assert _MATRIX.exists(), "BL-016 requires docs/nlp_capability_matrix.md"
    lines = _MATRIX.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Layer |"))
    headers = [cell.strip() for cell in lines[start].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(dict(zip(headers, cells, strict=True)))
    return rows


def _path_from_ref(ref: str) -> Path:
    return _ROOT / ref.split("::", 1)[0]


def _source_for_graphql_type(type_name: str) -> str:
    if type_name == "Query":
        return _QUERY.read_text(encoding="utf-8") + "".join(
            m.read_text(encoding="utf-8") for m in _QUERY_MIXINS
        )
    return _TYPES.read_text(encoding="utf-8") + _DOCUMENT_TYPES.read_text(encoding="utf-8")


def test_matrix_covers_all_twenty_nlp_layers_with_status_and_evidence():
    rows = _table()
    layers = {row["Layer"] for row in rows}
    assert layers == {str(i) for i in range(1, 21)}
    for row in rows:
        assert row["Capability"]
        assert row["Schema"]
        assert row["Writer"]
        assert row["Read adapter"]
        assert row["GraphQL"]
        assert row["Browser"]
        assert row["Health"]
        assert row["Tests"]
        assert row["Status"] in {
            "api-exposed",
            "ui-exposed",
            "writer-backed",
            "schema-only",
            "planned-only",
        }


def test_api_exposed_rows_resolve_to_live_graphql_fields():
    for row in _table():
        if row["GraphQL"].startswith("none:"):
            continue
        refs = _GRAPHQL_REF_RE.findall(row["GraphQL"])
        assert refs, f"Layer {row['Layer']} marks GraphQL exposure without a field ref"
        for type_name, field_name in refs:
            source = _source_for_graphql_type(type_name)
            assert f"class {type_name}" in source
            assert f"def {field_name}(" in source


def test_writer_backed_rows_cite_live_production_and_test_paths():
    for row in _table():
        if row["Writer"].startswith("none:"):
            continue
        writer_refs = _REF_RE.findall(row["Writer"])
        assert writer_refs, f"Layer {row['Layer']} has writer text without a path ref"
        assert _path_from_ref(writer_refs[0]).exists(), row["Writer"]

        test_refs = _REF_RE.findall(row["Tests"])
        assert test_refs, f"Layer {row['Layer']} has writer evidence but no test path"
        assert any(_path_from_ref(ref).exists() for ref in test_refs), row["Tests"]


def test_bl016_is_marked_done_only_when_matrix_exists():
    text = _BACKLOG.read_text(encoding="utf-8")
    bl016 = text.split('id:          "BL-016"', 1)[1].split('id:          "BL-017"', 1)[0]
    assert 'status:       "done"' in bl016
