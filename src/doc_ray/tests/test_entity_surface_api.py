"""GraphQL contracts for exact-surface NER correction previews and writes."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from doc_ray.entrypoints.api.schema import build_schema
from doc_ray.entrypoints.api.types import EntityMentionCorrection, Job

_DOC_A = "11111111-1111-1111-1111-111111111111"
_DOC_B = "22222222-2222-2222-2222-222222222222"


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        conn="caller-connection",
        principal=SimpleNamespace(principal_id="curator-1", scopes=frozenset({"write"})),
    )


def _job(document_id: str, ordinal: int) -> Job:
    return Job(
        id=str(ordinal),
        kind="apply_corrections",
        status="queued",
        document_id=document_id,
        created_at=datetime(2026, 7, 27, tzinfo=UTC),
        started_at=None,
        finished_at=None,
        attempts=0,
        max_attempts=3,
        error=None,
        result=None,
        progress_current=None,
        progress_total=None,
        progress_stage=None,
        cursor=str(ordinal),
    )


def test_schema_exposes_explicit_bounded_preview_and_surface_write() -> None:
    sdl = build_schema(debug=True).as_str()

    assert "entitySurfaceOccurrences(" in sdl
    assert "documentIds: [ID!]!" in sdl
    assert "surface: String!" in sdl
    assert "originalType: String!" in sdl
    assert "sampleLimit: Int! = 5" in sdl
    assert "countCap: Int! = 1000" in sdl
    assert "): [EntitySurfaceOccurrence!]!" in sdl
    assert "correctEntitySurface(" in sdl
    assert "): [Job!]!" in sdl

    correction = sdl.split("type EntityMentionCorrection {", 1)[1].split("}", 1)[0]
    assert "charStart: Int" in correction and "charStart: Int!" not in correction
    assert "charEnd: Int" in correction and "charEnd: Int!" not in correction
    assert "surfaceOriginalType: String" in correction


def test_preview_delegates_with_only_valid_explicit_document_ids(monkeypatch) -> None:
    from doc_ray.entrypoints.api import correction_query

    calls = []

    async def read(conn, document_ids, *, surface, original_type, sample_limit, count_cap):
        calls.append((conn, document_ids, surface, original_type, sample_limit, count_cap))
        return [
            {
                "document_id": _DOC_A,
                "surface": "Apple",
                "original_type": "ORG",
                "occurrence_count": 2,
                "count_cap": 1000,
                "count_capped": False,
                "samples": [
                    {
                        "mention_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                        "char_start": 4,
                        "char_end": 9,
                        "sentence_ordinal": 1,
                        "sentence_context": "Apple shipped it.",
                    }
                ],
            }
        ]

    monkeypatch.setattr(correction_query.surface_read, "entity_surface_occurrences", read)
    schema = build_schema(debug=True)

    async def execute():
        return await schema.execute(
            """
            query($ids: [ID!]!) {
              entitySurfaceOccurrences(
                documentIds: $ids, surface: "Apple", originalType: "ORG"
              ) {
                documentId surface originalType occurrenceCount countCap countCapped
                samples { mentionId charStart charEnd sentenceOrdinal sentenceContext }
              }
            }
            """,
            variable_values={"ids": [_DOC_A]},
            context_value=_context(),
        )

    result = asyncio.run(execute())
    assert result.errors is None, result.errors
    assert calls == [("caller-connection", [_DOC_A], "Apple", "ORG", 5, 1000)]
    preview = result.data["entitySurfaceOccurrences"][0]
    assert preview["occurrenceCount"] == 2
    assert preview["samples"][0]["sentenceContext"] == "Apple shipped it."


def test_preview_rejects_malformed_or_implicit_scope_before_read(monkeypatch) -> None:
    from doc_ray.entrypoints.api import correction_query

    async def fail(*args, **kwargs):
        raise AssertionError("invalid scope must not reach the read adapter")

    monkeypatch.setattr(correction_query.surface_read, "entity_surface_occurrences", fail)
    schema = build_schema(debug=True)

    async def execute(ids):
        return await schema.execute(
            "query($ids: [ID!]!) { entitySurfaceOccurrences("
            'documentIds: $ids, surface: "Apple", originalType: "ORG") { documentId } }',
            variable_values={"ids": ids},
            context_value=_context(),
        )

    empty = asyncio.run(execute([]))
    malformed = asyncio.run(execute(["not-a-uuid"]))
    assert empty.errors and "at least one" in str(empty.errors[0]).lower()
    assert malformed.errors and "document id" in str(malformed.errors[0]).lower()

    too_many = asyncio.run(execute([str(uuid.UUID(int=value + 1)) for value in range(201)]))
    assert too_many.errors and "at most 200" in str(too_many.errors[0]).lower()


def test_correction_audit_query_maps_surface_rows_and_ignores_malformed_ids(monkeypatch) -> None:
    from doc_ray.entrypoints.api import correction_query

    calls = []

    async def list_rows(conn, document_id):
        calls.append((conn, document_id))
        return [
            {
                "id": "correction-1",
                "document_id": document_id,
                "action": "reject_surface",
                "char_start": None,
                "char_end": None,
                "entity_type": None,
                "surface": "Apple",
                "new_char_start": None,
                "new_char_end": None,
                "surface_original_type": "ORG",
                "original_type": "ORG",
                "rationale": "ambiguous",
                "created_at": datetime(2026, 7, 27, tzinfo=UTC),
            }
        ]

    monkeypatch.setattr(
        correction_query.ner_corrections_store, "list_correction_rows_async", list_rows
    )
    schema = build_schema(debug=True)

    async def execute(document_id):
        return await schema.execute(
            "query($id: ID!) { entityMentionCorrections(documentId: $id) { "
            "action charStart charEnd surface surfaceOriginalType } }",
            variable_values={"id": document_id},
            context_value=_context(),
        )

    valid = asyncio.run(execute(_DOC_A))
    malformed = asyncio.run(execute("not-a-uuid"))
    assert valid.errors is None
    assert valid.data["entityMentionCorrections"][0] == {
        "action": "reject_surface",
        "charStart": None,
        "charEnd": None,
        "surface": "Apple",
        "surfaceOriginalType": "ORG",
    }
    assert malformed.errors is None
    assert malformed.data == {"entityMentionCorrections": []}
    assert calls == [("caller-connection", _DOC_A)]


def test_surface_mutation_authorizes_every_document_before_any_write(monkeypatch) -> None:
    from doc_ray.entrypoints.api import mutation_corrections

    events: list[tuple[object, ...]] = []

    async def guard(ctx, document_id, *, action):
        events.append(("guard", document_id, action))

    async def record(conn, document_id, correction, **kwargs):
        events.append(("record", document_id, correction, kwargs))
        return f"correction-{document_id[-1]}"

    async def enqueue(ctx, kind, payload, *, document_id):
        events.append(("enqueue", document_id, kind, payload))
        return f"job-{document_id[-1]}"

    async def status(ctx, job_id):
        document_id = _DOC_A if job_id.endswith("1") else _DOC_B
        return _job(document_id, 1 if document_id == _DOC_A else 2)

    async def audit(ctx, **kwargs):
        events.append(("audit", kwargs))

    monkeypatch.setattr(mutation_corrections, "_require_document_write", guard)
    monkeypatch.setattr(
        mutation_corrections.ner_corrections_store, "record_correction_async", record
    )
    monkeypatch.setattr(mutation_corrections, "_enqueue_job", enqueue)
    monkeypatch.setattr(mutation_corrections, "_job_status", status)
    monkeypatch.setattr(mutation_corrections, "_audit_mutation_event", audit)
    schema = build_schema(debug=True)

    async def execute():
        return await schema.execute(
            """
            mutation($ids: [ID!]!) {
              correctEntitySurface(
                documentIds: $ids, surface: "Apple", originalType: "ORG",
                action: "reclassify_surface", entityType: "PRODUCT", rationale: "brand"
              ) { kind status documentId }
            }
            """,
            variable_values={"ids": [_DOC_A, _DOC_B]},
            context_value=_context(),
        )

    result = asyncio.run(execute())
    assert result.errors is None, result.errors
    assert [event[:2] for event in events[:2]] == [("guard", _DOC_A), ("guard", _DOC_B)]
    assert events[2][0] == "record"
    correction = events[2][2]
    assert correction.char_start is None and correction.char_end is None
    assert correction.surface == "Apple"
    assert correction.surface_original_type == "ORG"
    assert correction.entity_type == "PRODUCT"
    kwargs = events[2][3]
    assert kwargs["reviewer"] == "curator-1"
    assert kwargs["original_type"] == "ORG"
    assert kwargs["rationale"] == "brand"
    audits = [event[1] for event in events if event[0] == "audit"]
    assert len(audits) == 2
    assert audits[0]["action"] == "mutation.correct_entity_surface"
    assert audits[0]["details"]["surface"] == "Apple"
    assert [row["documentId"] for row in result.data["correctEntitySurface"]] == [
        _DOC_A,
        _DOC_B,
    ]


def test_surface_mutation_refuses_non_surface_action_before_authorization(monkeypatch) -> None:
    from doc_ray.entrypoints.api import mutation_corrections

    calls = []

    async def guard(*args, **kwargs):
        calls.append((args, kwargs))

    async def audit(*args, **kwargs):
        calls.append(("audit", kwargs))

    monkeypatch.setattr(mutation_corrections, "_require_document_write", guard)
    monkeypatch.setattr(mutation_corrections, "_audit_mutation_event", audit)
    schema = build_schema(debug=True)

    async def execute():
        return await schema.execute(
            "mutation($ids: [ID!]!) { correctEntitySurface("
            'documentIds: $ids, surface: "Apple", originalType: "ORG", action: "reject") '
            "{ id } }",
            variable_values={"ids": [_DOC_A]},
            context_value=_context(),
        )

    result = asyncio.run(execute())
    assert result.errors and "surface action" in str(result.errors[0]).lower()
    assert not [call for call in calls if call[0] != "audit"]


def test_surface_mutation_rejects_empty_and_overwide_document_scope() -> None:
    schema = build_schema(debug=True)

    async def execute(ids):
        return await schema.execute(
            "mutation($ids: [ID!]!) { correctEntitySurface("
            'documentIds: $ids, surface: "Apple", originalType: "ORG", '
            'action: "reject_surface") { id } }',
            variable_values={"ids": ids},
            context_value=_context(),
        )

    empty = asyncio.run(execute([]))
    overwide = asyncio.run(execute([str(uuid.UUID(int=value + 1)) for value in range(201)]))
    assert empty.errors and "at least one" in str(empty.errors[0]).lower()
    assert overwide.errors and "at most 200" in str(overwide.errors[0]).lower()


def test_existing_span_mutation_remains_validated_audited_and_enqueued(monkeypatch) -> None:
    from doc_ray.entrypoints.api import mutation_corrections

    events = []

    async def guard(ctx, document_id, *, action):
        events.append(("guard", document_id, action))

    async def record(conn, document_id, correction, **kwargs):
        events.append(("record", document_id, correction, kwargs))
        return "correction-span"

    async def enqueue(ctx, kind, payload, *, document_id):
        events.append(("enqueue", document_id, kind, payload))
        return "job-span"

    async def status(ctx, job_id):
        return _job(_DOC_A, 1)

    async def audit(ctx, **kwargs):
        events.append(("audit", kwargs))

    monkeypatch.setattr(mutation_corrections, "_require_document_write", guard)
    monkeypatch.setattr(
        mutation_corrections.ner_corrections_store, "record_correction_async", record
    )
    monkeypatch.setattr(mutation_corrections, "_enqueue_job", enqueue)
    monkeypatch.setattr(mutation_corrections, "_job_status", status)
    monkeypatch.setattr(mutation_corrections, "_audit_mutation_event", audit)
    schema = build_schema(debug=True)

    async def execute(entity_type):
        return await schema.execute(
            "mutation($id: ID!, $type: String) { correctEntityMention("
            'documentId: $id, action: "reclassify", charStart: 4, charEnd: 9, '
            'entityType: $type, originalType: "ORG") { kind documentId } }',
            variable_values={"id": _DOC_A, "type": entity_type},
            context_value=_context(),
        )

    success = asyncio.run(execute("PRODUCT"))
    assert success.errors is None, success.errors
    assert events[0][:2] == ("guard", _DOC_A)
    correction = next(event[2] for event in events if event[0] == "record")
    assert correction.char_start == 4 and correction.entity_type == "PRODUCT"
    assert any(event[0] == "enqueue" for event in events)
    assert any(event[0] == "audit" and event[1]["outcome"] == "success" for event in events)

    events.clear()
    invalid = asyncio.run(execute(None))
    assert invalid.errors and "requires an entity_type" in str(invalid.errors[0])
    assert events[0][0] == "guard"
    assert any(event[0] == "audit" and event[1]["outcome"] == "denied" for event in events)
    assert not any(event[0] in {"record", "enqueue"} for event in events)


def test_correction_readback_maps_surface_matcher_and_nullable_spans() -> None:
    correction = EntityMentionCorrection.from_row(
        {
            "id": "correction-1",
            "document_id": _DOC_A,
            "action": "reject_surface",
            "char_start": None,
            "char_end": None,
            "entity_type": None,
            "surface": "Apple",
            "new_char_start": None,
            "new_char_end": None,
            "surface_original_type": "ORG",
            "original_type": "ORG",
            "rationale": "company, not fruit",
            "created_at": datetime(2026, 7, 27, tzinfo=UTC),
        }
    )

    assert correction.char_start is None and correction.char_end is None
    assert correction.surface_original_type == "ORG"


def test_adjust_span_docs_explain_optional_same_step_retyping() -> None:
    from doc_ray.entrypoints.api.mutation_corrections import CorrectionMutations

    doc = " ".join((CorrectionMutations.correct_entity_mention.__doc__ or "").split()).lower()
    assert "adjust_span" in doc
    assert "entity_type" in doc
    assert "retype" in doc
