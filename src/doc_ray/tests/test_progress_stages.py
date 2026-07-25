"""BL-123 gap: within-job progress must ADVANCE, not sit at a single stage.

The first landing reported one coarse ``0/1`` stage per handler, so a
248-page annotate showed a frozen 0% for its whole runtime. These tests pin
the fix: the reporter lives in ``domain.progress`` (importable from adapters
without breaking the hexagonal import direction), the spaCy annotator emits
advancing phase boundaries, and semantic enrichment reports per layer.
"""

from __future__ import annotations

from typing import Any

import pytest

from doc_ray.domain import progress as domain_progress


def _capture() -> tuple[list[tuple[int | None, int | None, str | None]], object]:
    events: list[tuple[int | None, int | None, str | None]] = []

    def reporter(current: int | None, total: int | None, stage: str | None) -> None:
        events.append((current, total, stage))

    token = domain_progress.set_progress_reporter(reporter)
    return events, token


def test_domain_reporter_forwards_and_is_noop_when_unset():
    events, token = _capture()
    try:
        domain_progress.report_progress(1, 4, "half way there")
        assert events == [(1, 4, "half way there")]
    finally:
        domain_progress.reset_progress_reporter(token)
    domain_progress.report_progress(2, 4, "dropped")
    assert events == [(1, 4, "half way there")]


def test_application_jobs_reexports_the_domain_reporter():
    from doc_ray.application import jobs

    events, token = _capture()
    try:
        jobs.report_progress(3, 7, "compat")
    finally:
        domain_progress.reset_progress_reporter(token)
    assert events == [(3, 7, "compat")]


def test_spacy_annotate_reports_advancing_phases(pg):
    import _itdb

    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    conn, created = pg
    doc_id = _itdb.make_doc(
        conn,
        "Elena founded a startup in Berlin. The company grew quickly. "
        "Investors from Paris joined the board in March.",
    )
    created.append(doc_id)

    events, token = _capture()
    try:
        SpacyAnnotator().annotate(conn, doc_id, lang_default="en")
    finally:
        domain_progress.reset_progress_reporter(token)

    assert events, "annotate() reported no progress at all"
    currents = [c for c, _t, _s in events if c is not None]
    totals = {t for _c, t, _s in events if t is not None}
    stages = {s for _c, _t, s in events if s}
    assert currents == sorted(currents), f"progress went backwards: {currents}"
    assert totals == {12}, f"expected a stable 12-phase scale, got {totals}"
    assert len(stages) >= 5, f"expected distinct phase stages, got {stages}"
    assert currents[0] == 0
    assert currents[-1] >= 11, "annotate() never reported persistence completion"


def test_spacy_annotate_reports_fine_grained_inner_phases(monkeypatch):
    from doc_ray.adapters import annotation_spacy
    from doc_ray.adapters.annotation_spacy import SpacyAnnotator

    text = "Elena founded Berlin."

    class _Rows:
        def __init__(
            self, *, one: dict[str, Any] | None = None, many: list[dict[str, Any]] | None = None
        ):
            self._one = one
            self._many = many or []

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._many

    class _Conn:
        def execute(self, sql: str, params=()):
            if "FROM document_content" in sql:
                return _Rows(one={"full_text": text})
            if "FROM sentences s" in sql:
                return _Rows(many=[{"id": "sent-1", "char_start": 0, "char_end": len(text)}])
            raise AssertionError(f"unexpected SQL: {sql}")

    class _Morph:
        def to_dict(self):
            return {"Number": "Sing"}

    class _Token:
        def __init__(self, text_: str, idx: int, i: int, pos: str):
            self.text = text_
            self.idx = idx
            self.i = i
            self.is_alpha = text_.isalpha()
            self.lemma_ = text_.lower()
            self.pos_ = pos
            self.tag_ = pos
            self.morph = _Morph()
            self.head = self
            self.dep_ = "ROOT"

    class _Ent:
        def __init__(self, start_char: int, end_char: int, label: str, surface: str):
            self.start_char = start_char
            self.end_char = end_char
            self.label_ = label
            self.text = surface

    class _Doc(list):
        @property
        def ents(self):
            return [_Ent(0, 5, "PERSON", "Elena"), _Ent(14, 20, "GPE", "Berlin")]

        @property
        def noun_chunks(self):
            return []

    doc = _Doc(
        [
            _Token("Elena", 0, 0, "PROPN"),
            _Token("founded", 6, 1, "VERB"),
            _Token("Berlin", 14, 2, "PROPN"),
            _Token(".", 20, 3, "PUNCT"),
        ]
    )
    span_pairs = [(0, 5), (6, 13), (14, 20), (20, 21)]
    span_pairs += [(0, 5), (14, 20)]
    span_map = {pair: f"span-{index}" for index, pair in enumerate(dict.fromkeys(span_pairs))}

    monkeypatch.setattr(annotation_spacy.lex, "detect_language", lambda *_args, **_kwargs: "en")
    monkeypatch.setattr(annotation_spacy.db, "get_or_create_lexeme", lambda *_args: ("lx", True))
    monkeypatch.setattr(annotation_spacy.db, "ensure_spans", lambda *_args: span_map)
    monkeypatch.setattr(
        SpacyAnnotator, "nlp_for", lambda *_args, **_kwargs: (lambda _text: doc, "spacy/fake@1")
    )
    monkeypatch.setattr(SpacyAnnotator, "_write", lambda *_args, **_kwargs: None)

    events, token = _capture()
    try:
        SpacyAnnotator().annotate(_Conn(), "doc-1", lang_default="en")
    finally:
        domain_progress.reset_progress_reporter(token)

    assert [(current, total) for current, total, _stage in events] == [
        (0, 12),
        (1, 12),
        (2, 12),
        (3, 12),
        (4, 12),
        (5, 12),
        (6, 12),
        (7, 12),
        (8, 12),
        (9, 12),
        (10, 12),
        (11, 12),
    ]
    assert [stage for _current, _total, stage in events] == [
        "Loading text and language model",
        "Running spaCy pipeline",
        "Indexing sentences",
        "Collecting token spans",
        "Collecting entity and chunk spans",
        "Registering 6 annotation spans",
        "Building token and morphology rows",
        "Building dependency, entity, chunk, and coreference rows",
        "Projecting lexeme occurrences",
        "Validating annotation output",
        "Persisting annotation layers",
        "Annotation layers persisted",
    ]


def test_deterministic_semantic_enrichment_reports_per_layer_progress(pg):
    import _itdb

    from doc_ray.application import semantic_enrichment

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "It rained on Tuesday. The launch was delayed.")
    created.append(doc_id)

    events, token = _capture()
    try:
        semantic_enrichment.annotate_document(conn, doc_id)
    finally:
        domain_progress.reset_progress_reporter(token)

    # Learned sentence embeddings have their own explicit semantic_embed job;
    # this deterministic pass now owns exactly four non-model semantic layers.
    layered = [(c, t, s) for c, t, s in events if t == 4]
    assert len(layered) >= 4, f"expected 4 layer reports on a /4 scale, got {events}"
    layer_currents = [c for c, _t, _s in layered if c is not None]
    assert layer_currents == sorted(layer_currents)
    assert layer_currents[0] == 0 and layer_currents[-1] == 3


def test_adapter_does_not_import_application_layer():
    """The instrumentation must not break the hexagonal import direction."""
    import ast
    from pathlib import Path

    source = Path("src/doc_ray/adapters/annotation_spacy.py").read_text(encoding="utf-8")
    offenders = [
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("doc_ray.application")
    ]
    assert not offenders, f"adapter imports application modules: {offenders}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
