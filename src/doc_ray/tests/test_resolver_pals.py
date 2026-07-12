"""Pure PALS-enforcement tests for the Claude resolver adapter (no SDK, no network).

The validator must coerce every malformed or out-of-set verdict to ABSTAIN — a
hallucinated id must never survive into a committed link.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from doc_ray.adapters.resolver_claude import (
    CandidateResolutionVerifier,
    ClaudeResolver,
    build_tool,
    to_resolution,
)
from doc_ray.application.verification import ModelOutputVerificationError, verify_model_output
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
        "new_entity_type",
        "aliases",
    }
    id_options = schema["properties"]["canonical_id"]["anyOf"]
    assert {"type": "integer", "enum": [7, 9]} in id_options  # the constrained choice


def test_tool_schema_without_candidates_only_admits_null_id():
    schema = build_tool([])["input_schema"]
    assert schema["properties"]["canonical_id"]["type"] == "null"


def test_claude_resolver_sets_an_explicit_sdk_timeout(monkeypatch):
    calls = []

    class FakeAnthropic:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    monkeypatch.setenv("DOCRAY_RESOLVER_TIMEOUT_SECONDS", "12.5")

    ClaudeResolver()

    assert calls == [{"timeout": 12.5}]


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


def test_schema_offers_entity_type_choice_for_new_entities():
    # The resolver may propose the entity type on NEW (fixes spaCy mistypes like
    # Clover-as-GPE); the choice is constrained to a closed enum, per PALS.
    schema = build_tool(CANDS)["input_schema"]
    assert "new_entity_type" in schema["required"]
    type_options = schema["properties"]["new_entity_type"]["anyOf"]
    enum = next(o for o in type_options if "enum" in o)
    assert "ANIMAL" in enum["enum"] and "PERSON" in enum["enum"]
    assert {"type": "null"} in type_options


def test_new_entity_carries_proposed_type():
    res = to_resolution(
        {
            "verdict": "new_entity",
            "new_preferred": "Clover",
            "new_entity_type": "ANIMAL",
            "confidence": 0.9,
        },
        CANDS,
    )
    assert res.kind is ResolutionKind.NEW
    assert res.new_entity_type == "ANIMAL"


def test_junk_proposed_type_is_dropped_not_fatal():
    res = to_resolution(
        {
            "verdict": "new_entity",
            "new_preferred": "Clover",
            "new_entity_type": 42,
            "confidence": 0.9,
        },
        CANDS,
    )
    assert res.kind is ResolutionKind.NEW  # the verdict survives
    assert res.new_entity_type is None  # the junk type does not


def test_candidate_resolution_verifier_rejects_hallucinated_ids_before_mapping():
    verifier = CandidateResolutionVerifier(CANDS)

    result = verifier.verify({"verdict": "select", "canonical_id": 12345, "confidence": 0.99})

    assert result.ok is False
    assert result.issues[0].code == "canonical-id-not-offered"
    try:
        verify_model_output(
            {"verdict": "select", "canonical_id": 12345, "confidence": 0.99}, verifier
        )
    except ModelOutputVerificationError as exc:
        assert exc.result.issues[0].code == "canonical-id-not-offered"
    else:  # pragma: no cover - defensive
        raise AssertionError("hallucinated candidate id was accepted")


def test_candidate_resolution_verifier_reports_malformed_output_fields():
    verifier = CandidateResolutionVerifier(CANDS)

    result = verifier.verify(
        {
            "verdict": "new_entity",
            "canonical_id": None,
            "confidence": "high",
            "aliases": "Berlin",
            "new_preferred": "",
        }
    )

    assert result.ok is False
    assert {issue.code for issue in result.issues} == {
        "invalid-confidence",
        "invalid-aliases",
        "missing-new-preferred",
    }


def test_claude_resolver_maps_valid_tool_call(monkeypatch):
    class FakeMessages:
        def create(self, **kwargs):
            assert kwargs["tool_choice"] == {"type": "tool", "name": "resolve_entity"}
            assert "Mention: 'Berlin'" in kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        input={
                            "verdict": "select",
                            "canonical_id": 7,
                            "confidence": 0.8,
                            "aliases": ["Berlin"],
                        },
                    )
                ]
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))

    result = ClaudeResolver(model="fake-model", timeout_seconds=1).resolve(
        "Berlin", "Berlin is in Germany.", CANDS
    )

    assert result.kind is ResolutionKind.SELECT
    assert result.canonical_id == 7


def test_claude_resolver_abstains_on_missing_or_invalid_tool_call(monkeypatch):
    class FakeMessages:
        def __init__(self, response):
            self.response = response

        def create(self, **kwargs):
            return self.response

    class FakeAnthropic:
        response = SimpleNamespace(content=[SimpleNamespace(type="text", input={})])

        def __init__(self, **kwargs):
            self.messages = FakeMessages(self.response)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    resolver = ClaudeResolver(model="fake-model", timeout_seconds=1)

    assert resolver.resolve("Berlin", "", CANDS).kind is ResolutionKind.ABSTAIN

    FakeAnthropic.response = SimpleNamespace(
        content=[
            SimpleNamespace(
                type="tool_use",
                input={"verdict": "select", "canonical_id": 12345, "confidence": 0.99},
            )
        ]
    )

    assert resolver.resolve("Berlin", "", CANDS).kind is ResolutionKind.ABSTAIN
