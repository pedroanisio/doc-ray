"""Guards for the mechanized feature inventory (scripts/gen_feature_inventory.py).

The inventory's only value is that it reports what the running system registers.
These tests protect the three ways that value can silently evaporate:

1. Collector health   — a surface that stops being introspectable must fail loudly,
   not shrink the report. A swallowed ImportError would quietly delete a whole
   capability class from the inventory.
2. Ground-truth anchors — capabilities known to exist from other evidence (the
   GraphQL mutation, the bound job handler, the PDF extractor) must appear, so the
   collectors cannot pass vacuously by returning nothing.
3. Evidence discipline — the scoring rules must actually constrain: a static
   declaration cannot reach `high`, and corroboration must not match on short
   common fragments.

Each rule has a self-test proving it can fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import gen_feature_inventory as inv
import pytest
from _feature_inventory_model import MIN_PROBE_LEN, CorroborationIndex, Feature

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inventory():
    """One full extraction run, shared by every test in this module."""
    results, features, index = inv.build_inventory()
    return results, features, index


def _by_category(features, category):
    return {f.name: f for f in features if f.category == category}


# --------------------------------------------------------------------------
# 1. Collector health
# --------------------------------------------------------------------------


def test_every_collector_succeeds(inventory):
    results, _, _ = inventory
    failures = {r.name: r.error for r in results if not r.ok}
    assert not failures, f"collectors failed (inventory would be partial): {failures}"


def test_every_collector_detects_something(inventory):
    results, _, _ = inventory
    empty = [r.name for r in results if not r.features]
    assert not empty, f"collectors returned no features — vacuous pass: {empty}"


def test_collector_failure_is_reported_not_raised(monkeypatch):
    """Self-test: a raising collector degrades the report instead of aborting it."""

    def boom():
        raise RuntimeError("surface unavailable")

    monkeypatch.setattr(inv, "COLLECTORS", (boom,))
    results = inv.run_collectors()
    assert len(results) == 1
    assert not results[0].ok
    assert "surface unavailable" in results[0].error


def test_exit_code_signals_partial_coverage(monkeypatch, capsys):
    """Self-test: a failed collector makes the CLI exit non-zero."""

    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(inv, "COLLECTORS", (boom,))
    assert inv.main(["--summary"]) == 1
    assert "1 of 1 collectors failed" in capsys.readouterr().out


# --------------------------------------------------------------------------
# 2. Ground-truth anchors
# --------------------------------------------------------------------------


def test_feature_ids_are_unique(inventory):
    _, features, _ = inventory
    ids = [f.id for f in features]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate feature ids collapse distinct capabilities: {duplicates}"


@pytest.mark.parametrize(
    ("category", "name"),
    [
        ("graphql.mutation", "archiveDocument"),
        ("graphql.query", "documents"),
        ("mcp.tool.read", "read_document_sentences"),
        ("mcp.tool.write", "update_document_metadata"),
        ("job.kind", "archive_document"),
        ("ingest.format", ".pdf"),
        ("annotation.layer", "named_entity_recognition"),
    ],
)
def test_known_capability_is_detected(inventory, category, name):
    _, features, _ = inventory
    assert name in _by_category(features, category)


def test_runtime_records_resolve_to_real_source_lines(inventory):
    """A reported file:line must exist and be inside the repo."""
    _, features, _ = inventory
    checked = 0
    for feature in features:
        if not feature.source_file or feature.category == "config.variable":
            continue
        path = _ROOT / feature.source_file
        assert path.is_file(), f"{feature.id} points at a missing file: {feature.source_file}"
        assert feature.source_line <= len(path.read_text(encoding="utf-8").splitlines()), (
            f"{feature.id} points past end of {feature.source_file}"
        )
        checked += 1
    assert checked > 100, "too few located records to call this guard meaningful"


def test_archive_is_a_job_and_a_graphql_mutation_but_not_an_mcp_tool(inventory):
    """Cross-surface asymmetry the inventory exists to make visible."""
    _, features, _ = inventory
    assert "archive_document" in _by_category(features, "job.kind")
    assert "archiveDocument" in _by_category(features, "graphql.mutation")
    mcp_tools = _by_category(features, "mcp.tool.read") | _by_category(features, "mcp.tool.write")
    assert "archive_document" not in mcp_tools


# --------------------------------------------------------------------------
# 3. Evidence discipline
# --------------------------------------------------------------------------


def test_runtime_evidence_outranks_declaration():
    runtime = Feature(
        id="a", name="a", category="c", surface="s", signals=["runtime-registry", "documented"]
    )
    static = Feature(
        id="b",
        name="b",
        category="c",
        surface="s",
        signals=["static-declaration", "documented", "source-located", "tested"],
    )
    assert runtime.evidence_score == 3
    assert runtime.confidence == "medium"
    # Same score band, but capped: a declaration never claims `high`.
    assert static.evidence_score == 4
    assert static.confidence == "medium"


def test_static_records_never_reach_high_confidence(inventory):
    _, features, _ = inventory
    offenders = [
        f.id for f in features if f.confidence == "high" and "runtime-registry" not in f.signals
    ]
    assert not offenders, offenders


def test_unsupported_capability_scores_low():
    """Self-test: strip the evidence and the band must drop."""
    bare = Feature(id="x", name="x", category="c", surface="s", signals=[])
    assert bare.evidence_score == 0
    assert bare.confidence == "low"


def test_corroboration_ignores_short_fragments(tmp_path):
    """Self-test: probes below the length floor must not corroborate anything."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test(): delete_all('KIND_WIDGET')\n")
    index = CorroborationIndex(root=tmp_path, test_roots=("tests",))
    assert index.file_count == 1
    assert index.count(["KIND_WIDGET"]) == 1
    assert index.count(["x" * (MIN_PROBE_LEN - 1)]) == 0
    # Word-boundary matching: a substring of a longer identifier is not a reference.
    assert index.count(["delete"]) == 0


def test_confidence_filter_narrows_the_selection(inventory):
    _, features, _ = inventory
    high = inv.filter_features(features, [], "high")
    everything = inv.filter_features(features, [], "low")
    assert 0 < len(high) < len(everything) == len(features)
    assert {f.confidence for f in high} == {"high"}


def test_category_filter_selects_one_surface(inventory):
    _, features, _ = inventory
    only_mcp = inv.filter_features(features, ["mcp.tool.read"], "low")
    assert only_mcp and {f.category for f in only_mcp} == {"mcp.tool.read"}


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------


def test_json_totals_match_the_record_count(inventory):
    results, features, index = inventory
    payload = json.loads(inv.render_json(results, features, index))
    assert payload["totals"]["features"] == len(features) == len(payload["features"])
    assert sum(payload["summary"][c]["total"] for c in payload["summary"]) == len(features)
    assert all(row["evidence"]["score"] >= 0 for row in payload["features"])


def test_markdown_carries_the_mandatory_disclaimer_and_counts(inventory):
    results, features, index = inventory
    text = inv.render_markdown(results, features, index, detail=False)
    assert text.startswith("---\ndisclaimer:")
    assert 'generated_by: "scripts/gen_feature_inventory.py' in text
    assert "## Extraction integrity" in text
    assert f"**{len(features)}**" in text
