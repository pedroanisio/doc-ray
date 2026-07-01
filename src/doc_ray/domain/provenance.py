"""Provenance event construction.

Builds the append-only audit trail for a document ingest as an ordered list of
event dicts, each keyed by a monotonic ULID so the log sorts chronologically.
Pure and dependency-free (no DB) so it is unit-testable in isolation; the DB
layer persists whatever this returns.
"""

from __future__ import annotations

from typing import Any

from doc_ray.domain.ulid import ULIDFactory

# Version of the segmentation step, recorded in provenance so re-runs are
# comparable. Bump when sentence-splitting behavior changes.
SEGMENTER = "ingest.sentences"
SEGMENTER_VERSION = "0.1"


def build_events(
    *,
    content_sha256: str,
    source_filename: str,
    byte_size: int,
    storage_bucket: str,
    storage_key: str,
    storage_uri: str,
    status: str,  # 'extracted' | 'failed'
    extractor: str | None = None,
    extractor_version: str | None = None,
    page_count: int | None = None,
    char_count: int | None = None,
    text_sha256: str | None = None,
    sentence_count: int | None = None,
    error: str | None = None,
    actor: str = "ingest",
    factory: ULIDFactory | None = None,
) -> list[dict[str, Any]]:
    """Return the ordered provenance events for one ingest.

    Happy path -> acquired, stored, extracted, segmented.
    Failed extraction -> acquired, stored, extracted(note=error).
    """
    f = factory or ULIDFactory()
    events: list[dict[str, Any]] = []

    def ev(kind: str, **fields: object) -> None:
        """Append one provenance event, merging ``fields`` over the defaults.

        Args:
            kind: The event kind (e.g. ``"acquired"`` or ``"stored"``).
            **fields: Overrides for the default event keys.
        """
        event: dict[str, Any] = {
            "id": str(f.new()),
            "kind": kind,
            "tool": None,
            "tool_version": None,
            "actor": actor,
            "input_sha256": None,
            "output_sha256": None,
            "params": {},
            "note": None,
        }
        event.update(fields)
        events.append(event)

    ev(
        "acquired",
        params={"source_filename": source_filename, "byte_size": byte_size},
    )
    ev(
        "stored",
        tool="object-store",
        output_sha256=content_sha256,
        params={"bucket": storage_bucket, "key": storage_key, "uri": storage_uri},
    )

    if status == "failed":
        ev(
            "extracted",
            tool=extractor,
            tool_version=extractor_version,
            input_sha256=content_sha256,
            note=error,
        )
        return events

    ev(
        "extracted",
        tool=extractor,
        tool_version=extractor_version,
        input_sha256=content_sha256,
        output_sha256=text_sha256,
        params={"page_count": page_count, "char_count": char_count},
    )
    ev(
        "segmented",
        tool=SEGMENTER,
        tool_version=SEGMENTER_VERSION,
        params={"sentences": sentence_count},
    )
    return events
