"""Pure PALS-enforcement tests for the Claude resolver adapter (no SDK, no network).

The validator must coerce every malformed or out-of-set verdict to ABSTAIN — a
hallucinated id must never survive into a committed link.
"""

from __future__ import annotations

from doc_ray.adapters.resolver_claude import build_tool, to_resolution
from doc_ray.ports.resolver import Candidate, ResolutionKind

CANDS = [
    Candidate(canonical_id=7, preferred="Berlin", entity_type="PLACE"),
    Candidate(canonical_id=9, preferred="Berlin, NH", entity_type="PLACE"),
]


def test_tool_schema_is_strict_and_enum_is_the_candidate_set():
    tool = build_tool(CANDS)
    assert tool["strict"] is True
    schema = tool["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "verdict",
        "canonical_id",
        "confidence",
        "new_preferred",
        "aliases",
    }
    id_options = schema["properties"]["canonical_id"]["anyOf"]
    assert {"type": "integer", "enum": [7, 9]} in id_options  # the constrained choice


def test_tool_schema_without_candidates_only_admits_null_id():
    schema = build_tool([])["input_schema"]
    assert schema["properties"]["canonical_id"]["type"] == "null"


def test_valid_select_passes():
    res = to_resolution(
        {"verdict": "select", "canonical_id": 9, "confidence": 0.93, "aliases": ["B."]},
        CANDS,
    )
    assert res.kind is ResolutionKind.SELECT
    assert res.canonical_id == 9
    assert res.confidence == 0.93
    assert res.harvested_aliases == ("B.",)


def test_hallucinated_id_is_coerced_to_abstain():
    res = to_resolution({"verdict": "select", "canonical_id": 12345, "confidence": 0.99}, CANDS)
    assert res.kind is ResolutionKind.ABSTAIN
    assert res.canonical_id is None


def test_boolean_id_is_not_an_id():
    res = to_resolution({"verdict": "select", "canonical_id": True, "confidence": 0.9}, CANDS)
    assert res.kind is ResolutionKind.ABSTAIN


def test_select_with_no_candidates_abstains():
    res = to_resolution({"verdict": "select", "canonical_id": 7, "confidence": 0.9}, [])
    assert res.kind is ResolutionKind.ABSTAIN


def test_new_entity_requires_a_name():
    res = to_resolution({"verdict": "new_entity", "new_preferred": "  ", "confidence": 0.9}, CANDS)
    assert res.kind is ResolutionKind.ABSTAIN
    ok = to_resolution(
        {"verdict": "new_entity", "new_preferred": " Elena Fischer ", "confidence": 0.9}, CANDS
    )
    assert ok.kind is ResolutionKind.NEW
    assert ok.new_preferred == "Elena Fischer"


def test_unknown_verdict_and_empty_input_abstain():
    assert to_resolution({"verdict": "pick_best"}, CANDS).kind is ResolutionKind.ABSTAIN
    assert to_resolution({}, CANDS).kind is ResolutionKind.ABSTAIN


def test_confidence_is_clamped_and_junk_is_zero():
    assert to_resolution({"verdict": "abstain", "confidence": 7}, CANDS).confidence == 1.0
    assert to_resolution({"verdict": "abstain", "confidence": -3}, CANDS).confidence == 0.0
    assert to_resolution({"verdict": "abstain", "confidence": "high"}, CANDS).confidence == 0.0
    assert to_resolution({"verdict": "abstain", "confidence": True}, CANDS).confidence == 0.0


def test_nan_and_inf_confidence_never_auto_commit():
    # min(1.0, nan) == 1.0 in Python — without an explicit guard a NaN confidence
    # would clamp to FULL confidence and auto-commit. It must be junk -> 0.0.
    nan = to_resolution({"verdict": "select", "canonical_id": 7, "confidence": float("nan")}, CANDS)
    assert nan.confidence == 0.0
    inf = to_resolution({"verdict": "select", "canonical_id": 7, "confidence": float("inf")}, CANDS)
    assert inf.confidence == 0.0


def test_alias_junk_is_filtered():
    res = to_resolution({"verdict": "abstain", "aliases": ["Ze", "", 42, "  ", " Zelya "]}, CANDS)
    assert res.harvested_aliases == ("Ze", "Zelya")
