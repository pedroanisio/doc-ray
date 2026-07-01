"""Tests for provenance event construction."""

from __future__ import annotations

import re

from doc_ray.domain.provenance import SEGMENTER, build_events
from doc_ray.domain.ulid import ULID_REGEX, ULIDFactory

_RE = re.compile(ULID_REGEX)

_COMMON = dict(
    content_sha256="a" * 64,
    source_filename="report.pdf",
    byte_size=1234,
    storage_bucket="documents",
    storage_key="ab/abcdef.pdf",
    storage_uri="s3://documents/ab/abcdef.pdf",
)


def _happy(**over):
    kw = dict(
        _COMMON,
        status="extracted",
        extractor="pypdf",
        extractor_version="4.3.1",
        page_count=10,
        char_count=5000,
        text_sha256="b" * 64,
        sentence_count=42,
    )
    kw.update(over)
    return build_events(**kw)


def test_happy_path_event_sequence():
    events = _happy()
    assert [e["kind"] for e in events] == [
        "acquired",
        "stored",
        "extracted",
        "segmented",
    ]


def test_failed_path_stops_at_extracted():
    events = build_events(
        **_COMMON, status="failed", extractor="pypdf", extractor_version="4.3.1", error="boom"
    )
    assert [e["kind"] for e in events] == ["acquired", "stored", "extracted"]
    extracted = events[-1]
    assert extracted["note"] == "boom"
    assert extracted["output_sha256"] is None  # nothing was produced


def test_all_events_have_valid_monotonic_ulids():
    events = _happy()
    ids = [e["id"] for e in events]
    assert all(_RE.match(i) for i in ids)
    assert ids == sorted(ids)  # chronological
    assert len(set(ids)) == len(ids)  # unique


def test_hash_chain_links_bytes_to_text():
    events = _happy()
    stored = next(e for e in events if e["kind"] == "stored")
    extracted = next(e for e in events if e["kind"] == "extracted")
    # stored records the byte hash; extracted consumes it and emits the text hash
    assert stored["output_sha256"] == "a" * 64
    assert extracted["input_sha256"] == "a" * 64
    assert extracted["output_sha256"] == "b" * 64


def test_extracted_and_segmented_carry_tool_metadata():
    events = _happy()
    extracted = next(e for e in events if e["kind"] == "extracted")
    segmented = next(e for e in events if e["kind"] == "segmented")
    assert extracted["tool"] == "pypdf"
    assert extracted["tool_version"] == "4.3.1"
    assert extracted["params"]["page_count"] == 10
    assert extracted["params"]["char_count"] == 5000
    assert segmented["tool"] == SEGMENTER
    assert segmented["params"]["sentences"] == 42


def test_stored_records_object_location():
    stored = next(e for e in _happy() if e["kind"] == "stored")
    assert stored["params"] == {
        "bucket": "documents",
        "key": "ab/abcdef.pdf",
        "uri": "s3://documents/ab/abcdef.pdf",
    }


def test_injected_factory_makes_ordering_deterministic():
    # Same frozen clock -> ids strictly increase by randomness in insertion order.
    events = _happy(factory=ULIDFactory(clock=lambda: 999, rng=lambda: 0))
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)
    assert len({i[:10] for i in ids}) == 1  # all in the same ms
