"""PALS-enforcement tests for the Ollama resolver adapter (no network).

The Ollama backend reuses the same constrained-choice schema and the same
``to_resolution`` verification as the Claude backend, so a weaker local model's
hallucination is coerced to ABSTAIN exactly the same way. A fake httpx client
stands in for the HTTP round-trip.
"""

from __future__ import annotations

import json

from doc_ray.adapters.resolver_ollama import OllamaResolver
from doc_ray.ports.resolver import Candidate, ResolutionKind

CANDS = [
    Candidate(canonical_id=7, preferred="Berlin", entity_type="PLACE"),
    Candidate(canonical_id=9, preferred="Berlin, NH", entity_type="PLACE"),
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """Records the request and returns a canned Ollama /api/chat payload."""

    def __init__(self, content):
        self.content = content
        self.calls = []

    def post(self, url, json):  # noqa: A002 - mirrors httpx.Client.post signature
        self.calls.append((url, json))
        return _FakeResponse({"message": {"role": "assistant", "content": self.content}})


def _resolver(content):
    return OllamaResolver(model="qwen2.5", host="http://ollama:11434", client=_FakeClient(content))


def test_ollama_request_is_a_constrained_json_chat_call():
    fake = _FakeClient(json.dumps({"verdict": "abstain", "confidence": 0.1}))
    OllamaResolver(model="qwen2.5", host="http://ollama:11434/", client=fake).resolve(
        "Berlin", "Berlin is in Germany.", CANDS
    )
    url, body = fake.calls[0]
    assert url == "http://ollama:11434/api/chat"  # trailing slash on host is stripped
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0  # determinism, not sampling
    assert body["model"] == "qwen2.5"
    # The JSON-schema `format` IS the strict candidate-constrained schema:
    assert body["format"]["additionalProperties"] is False
    assert {"type": "integer", "enum": [7, 9]} in body["format"]["properties"]["canonical_id"][
        "anyOf"
    ]
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"].startswith("Mention: 'Berlin'")


def test_ollama_maps_a_valid_select_verdict():
    content = json.dumps(
        {"verdict": "select", "canonical_id": 7, "confidence": 0.8, "aliases": ["Berlin"]}
    )
    res = _resolver(content).resolve("Berlin", "Berlin is in Germany.", CANDS)
    assert res.kind is ResolutionKind.SELECT
    assert res.canonical_id == 7
    assert res.harvested_aliases == ("Berlin",)


def test_ollama_hallucinated_id_is_coerced_to_abstain():
    content = json.dumps({"verdict": "select", "canonical_id": 999, "confidence": 0.99})
    assert _resolver(content).resolve("Berlin", "", CANDS).kind is ResolutionKind.ABSTAIN


def test_ollama_empty_or_nonjson_content_abstains():
    assert _resolver("").resolve("Berlin", "", CANDS).kind is ResolutionKind.ABSTAIN
    assert _resolver("not json at all").resolve("Berlin", "", CANDS).kind is ResolutionKind.ABSTAIN


def test_ollama_missing_message_abstains():
    class _EmptyClient:
        def post(self, url, json):
            return _FakeResponse({})

    r = OllamaResolver(model="qwen2.5", host="http://ollama:11434", client=_EmptyClient())
    assert r.resolve("Berlin", "", CANDS).kind is ResolutionKind.ABSTAIN


def test_ollama_name_records_the_backend_and_model():
    assert _resolver("{}").name == "ollama/qwen2.5"
