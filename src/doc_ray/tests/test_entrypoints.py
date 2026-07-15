"""CLI entrypoints: ingest cli, migration runner, and reextract (with reannotate)."""

from __future__ import annotations

import importlib
import runpy

import _itdb
import pytest

from doc_ray.application import pipeline
from doc_ray.application import reextract as reextract_app
from doc_ray.entrypoints import cli as cli_mod
from doc_ray.entrypoints import migrate as migrate_ep
from doc_ray.entrypoints import reextract as reextract_ep


class FakeStore:
    """In-memory ObjectStore double for path-based ingestion."""

    def __init__(self, cfg=None) -> None:
        self.bucket = "test-bucket"
        self.blobs: dict[str, bytes] = {}

    def ensure_bucket(self) -> None:
        return None

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.blobs[key] = data
        return f"s3://{self.bucket}/{key}"

    def get(self, key: str) -> bytes:
        return self.blobs[key]


class _ConnContext:
    def __init__(self, conn) -> None:
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_cli_ingests_and_reports(pg, tmp_path, monkeypatch, capsys):
    conn, created = pg
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)
    f = tmp_path / f"cli-{_itdb.unique_sha()[:8]}.txt"
    f.write_text("From the CLI. It works.", encoding="utf-8")

    rc = cli_mod.main([str(f)])
    out = capsys.readouterr().out
    assert rc == 0 and "[OK ]" in out and "ingested=1" in out
    doc_id = out.split("-> ")[1].split()[0]
    created.append(doc_id)


def test_cli_no_supported_files(tmp_path, capsys):
    assert cli_mod.main([str(tmp_path)]) == 1
    assert "No supported documents" in capsys.readouterr().out


def test_migrate_dry_run_lists_pending(capsys):
    assert migrate_ep.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "pending" in out  # "<n> pending" — all applied on the live DB


def test_migrate_unreachable_database(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@localhost:1/void")
    assert migrate_ep.main(["--dry-run"]) == 2
    assert "unreachable" in capsys.readouterr().err


def test_reextract_rebuilds_layers_and_reannotates(pg, tmp_path, monkeypatch):
    conn, created = pg
    monkeypatch.setattr(pipeline, "ObjectStore", FakeStore)
    text = "Elena founded a startup. It thrived in Berlin."
    f = tmp_path / f"rex-{_itdb.unique_sha()[:8]}.txt"
    f.write_text(text, encoding="utf-8")

    r = pipeline.ingest_file(conn, FakeStore(), f)
    created.append(r.document_id)

    # Rebuild the derived layers from the same bytes (the CLI's core step),
    # then re-annotate with real spaCy (the CLI's second step).
    info = reextract_app.reextract_document(
        conn, r.document_id, f.read_bytes(), filename=f.stem, ext=".txt"
    )
    assert info["sentences"] == 2 and info["chars"] == len(text)

    lang, occ = reextract_app._reannotate(conn, r.document_id)
    assert lang == "en" and occ > 0  # real spaCy lexeme layer landed

    n = conn.execute(
        "SELECT count(*) AS n FROM lexeme_occurrences o JOIN annotations a ON a.id=o.annotation_id "
        "WHERE a.document_id=%s",
        (r.document_id,),
    ).fetchone()["n"]
    assert n == occ


def test_reextract_refuses_wrong_bytes(pg, tmp_path, monkeypatch, capsys):
    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Original text here.")
    created.append(doc_id)
    title = f"itrex-{_itdb.unique_sha()[:8]}"
    conn.execute("UPDATE documents SET title=%s WHERE id=%s", (title, doc_id))
    other = tmp_path / "other.txt"
    other.write_text("Different bytes entirely.", encoding="utf-8")

    assert reextract_ep.main(["--title", title, "--file", str(other)]) == 1
    assert "sha256 does not match" in capsys.readouterr().err


def test_reextract_unknown_title(tmp_path, capsys):
    f = tmp_path / "x.txt"
    f.write_text("x", encoding="utf-8")
    assert reextract_ep.main(["--title", "no-such-doc-zzz", "--file", str(f)]) == 1
    assert "No document titled" in capsys.readouterr().err


def test_migrate_apply_path_with_nothing_pending(capsys):
    # All migrations are applied on the live DB, so the apply path is a safe no-op.
    assert migrate_ep.main([]) == 0
    assert "0 applied" in capsys.readouterr().out


def test_rule_annotation_cli_single_document(pg, capsys):
    from doc_ray.adapters import annotation as annotation_mod

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "The quick brown fox jumps over the lazy dog.")
    created.append(doc_id)
    assert annotation_mod.main(["--id", doc_id]) == 0
    out = capsys.readouterr().out
    assert "occurrences" in out and "distinct lexemes" in out
    n = conn.execute(
        "SELECT count(*) AS n FROM lexeme_occurrences o JOIN annotations a ON a.id=o.annotation_id "
        "WHERE a.document_id=%s",
        (doc_id,),
    ).fetchone()["n"]
    assert n > 0


def test_rule_annotation_cli_no_match(monkeypatch, capsys):
    from doc_ray.adapters import annotation as annotation_mod

    assert annotation_mod.main(["--filename", "never-ingested-zzz.pdf"]) == 1
    assert "No matching documents" in capsys.readouterr().err


def test_spacy_reannotate_cli_single_document(pg, capsys):
    from doc_ray.application import reannotate as reannotate_app

    conn, created = pg
    doc_id = _itdb.make_doc(conn, "Elena founded a startup in Berlin.")
    created.append(doc_id)
    assert reannotate_app.main(["--id", doc_id]) == 0
    out = capsys.readouterr().out
    assert "lang=en" in out and "distinct lexemes" in out


def test_spacy_annotation_cli_reports_each_selected_document(monkeypatch, capsys):
    from doc_ray.adapters import annotation_spacy_cli as spacy_cli

    class Conn:
        def execute(self, query, params=()):
            text = " ".join(query.split())
            if "WHERE original_filename=%s" in text:
                return type("Rows", (), {"fetchall": lambda self: [{"id": "doc-1"}]})()
            if "left(coalesce(original_filename,title),36)" in text:
                return type("Row", (), {"fetchone": lambda self: {"t": "sample.pdf"}})()
            raise AssertionError(text)

    class Annotator:
        def annotate(self, conn, doc_id):
            assert doc_id == "doc-1"
            return {"tokens": 7, "entities": 2, "chunks": 1, "coref_chains": 0, "tool": "fake"}

    monkeypatch.setattr(spacy_cli.db, "connect", lambda _cfg: _ConnContext(Conn()))
    monkeypatch.setattr(spacy_cli, "SpacyAnnotator", Annotator)

    assert spacy_cli.main(["--filename", "sample.pdf"]) == 0

    out = capsys.readouterr().out
    assert "sample.pdf" in out
    assert "7 tok" in out
    assert "[fake]" in out


def test_spacy_annotation_cli_records_detected_language(monkeypatch, capsys):
    """The CLI must persist the annotator's detected language like the worker does.

    Without this the ``documents.language`` column stays NULL, so migration-016's
    trigger rebuilds the FTS tsvector with the language-agnostic ``simple`` config
    and stemmed search degrades (corpus_health flags ``fts-degraded``).
    """
    from doc_ray.adapters import annotation_spacy_cli as spacy_cli

    updates: list[tuple[str, tuple]] = []

    class Conn:
        def execute(self, query, params=()):
            text = " ".join(query.split())
            if "WHERE original_filename=%s" in text:
                return type("Rows", (), {"fetchall": lambda self: [{"id": "doc-1"}]})()
            if "left(coalesce(original_filename,title),36)" in text:
                return type("Row", (), {"fetchone": lambda self: {"t": "sample.pdf"}})()
            if "UPDATE documents SET language" in text:
                updates.append((text, params))
                return type("R", (), {"fetchone": lambda self: None})()
            raise AssertionError(text)

    class Annotator:
        def annotate(self, conn, doc_id):
            return {
                "tokens": 7,
                "entities": 2,
                "chunks": 1,
                "coref_chains": 0,
                "tool": "fake",
                "lang": "en",
            }

    monkeypatch.setattr(spacy_cli.db, "connect", lambda _cfg: _ConnContext(Conn()))
    monkeypatch.setattr(spacy_cli, "SpacyAnnotator", Annotator)

    assert spacy_cli.main(["--filename", "sample.pdf"]) == 0

    assert len(updates) == 1, "CLI must persist the detected language"
    _text, params = updates[0]
    assert "en" in params and "doc-1" in params


def test_spacy_annotation_cli_reports_no_documents(monkeypatch, capsys):
    from doc_ray.adapters import annotation_spacy_cli as spacy_cli

    class Conn:
        def execute(self, query, params=()):
            return type("Rows", (), {"fetchall": lambda self: []})()

    monkeypatch.setattr(spacy_cli.db, "connect", lambda _cfg: _ConnContext(Conn()))

    assert spacy_cli.main(["--all"]) == 1
    assert "No documents." in capsys.readouterr().err


def test_semantic_annotation_cli_accumulates_document_totals(monkeypatch, capsys):
    from doc_ray.entrypoints import annotate_semantic

    class Conn:
        def execute(self, query, params=()):
            return type(
                "Rows",
                (),
                {"fetchall": lambda self: [{"id": "doc-1"}, {"id": "doc-2"}]},
            )()

    summaries = {
        "doc-1": {
            "discourse_relations": 1,
            "timexes": 2,
            "events": 3,
            "sentiments": 4,
            "embeddings": 5,
        },
        "doc-2": {
            "discourse_relations": 10,
            "timexes": 20,
            "events": 30,
            "sentiments": 40,
            "embeddings": 50,
        },
    }
    monkeypatch.setattr(annotate_semantic.db, "connect", lambda _cfg: _ConnContext(Conn()))
    monkeypatch.setattr(
        annotate_semantic.semantic_enrichment,
        "annotate_document",
        lambda _conn, doc_id: summaries[doc_id],
    )

    assert annotate_semantic.main(["--all"]) == 0

    out = capsys.readouterr().out
    assert "doc-1: discourse=1 timex=2 events=3 sentiment=4 embeddings=5" in out
    assert "Done: discourse=11 timex=22 events=33 sentiment=44 embeddings=55" in out


def test_semantic_annotation_cli_returns_one_when_selector_matches_nothing(monkeypatch, capsys):
    from doc_ray.entrypoints import annotate_semantic

    monkeypatch.setattr(annotate_semantic.db, "connect", lambda _cfg: _ConnContext(object()))

    assert annotate_semantic.main(["--id", ""]) == 1
    assert "No matching documents." in capsys.readouterr().err


def test_evaluate_cli_prints_json_payload(monkeypatch, capsys):
    from doc_ray.entrypoints import evaluate

    result = type("Result", (), {"snapshot_id": "snap-1", "run_id": "run-1"})()
    monkeypatch.setattr(evaluate.db, "connect", lambda _cfg: _ConnContext(object()))
    monkeypatch.setattr(
        evaluate.evaluation,
        "create_evaluation_run",
        lambda _conn, **kwargs: result,
    )

    assert evaluate.main(["--source", "nightly", "--json"]) == 0

    out = capsys.readouterr().out
    assert '"snapshot_id": "snap-1"' in out
    assert '"run_id": "run-1"' in out


def test_backup_scheduler_main_runs_one_pass(monkeypatch, capsys):
    from doc_ray.entrypoints import backup_scheduler

    cfg = type(
        "Cfg",
        (),
        {
            "backup_schedule_seconds": 99,
            "backup_retention_count": 3,
            "backup_retention_days": 30,
        },
    )()

    class Store:
        def __init__(self, cfg) -> None:
            self.cfg = cfg
            self.ensured = False

        def ensure_bucket(self) -> None:
            self.ensured = True

    monkeypatch.setattr(backup_scheduler.Config, "from_env", lambda: cfg)
    monkeypatch.setattr(backup_scheduler, "ObjectStore", Store)
    monkeypatch.setattr(backup_scheduler.db, "connect", lambda _cfg: _ConnContext(object()))
    monkeypatch.setattr(
        backup_scheduler,
        "run_once",
        lambda _conn, _store, **kwargs: {
            "job_id": "job-1",
            "active_job_id": None,
            "pruned": [{"id": "backup-1"}],
        },
    )

    assert backup_scheduler.main(["--once"]) == 0

    assert "job_id=job-1 active_job_id=None pruned=1" in capsys.readouterr().out


def test_backup_scheduler_rejects_non_positive_interval(monkeypatch):
    from doc_ray.entrypoints import backup_scheduler

    cfg = type(
        "Cfg",
        (),
        {
            "backup_schedule_seconds": 99,
            "backup_retention_count": 3,
            "backup_retention_days": 30,
        },
    )()
    monkeypatch.setattr(backup_scheduler.Config, "from_env", lambda: cfg)

    with pytest.raises(SystemExit) as exc:
        backup_scheduler.main(["--interval", "0"])

    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("module_name", "target_name"),
    [
        ("doc_ray.entrypoints.annotate", "doc_ray.adapters.annotation"),
        ("doc_ray.entrypoints.annotate_spacy", "doc_ray.adapters.annotation_spacy_cli"),
        ("doc_ray.entrypoints.corpus_health", "doc_ray.application.corpus_health"),
        ("doc_ray.entrypoints.db_analyze", "doc_ray.application.db_analyzer.run"),
        ("doc_ray.entrypoints.db_health", "doc_ray.application.db_health"),
        ("doc_ray.entrypoints.export", "doc_ray.adapters.export"),
        ("doc_ray.entrypoints.reannotate", "doc_ray.application.reannotate"),
        ("doc_ray.entrypoints.reextract", "doc_ray.application.reextract"),
    ],
)
def test_thin_entrypoint_modules_exit_with_delegated_main(module_name, target_name, monkeypatch):
    target = importlib.import_module(target_name)
    calls: list[bool] = []

    def fake_main() -> int:
        calls.append(True)
        return 17

    monkeypatch.setattr(target, "main", fake_main)

    with pytest.raises(SystemExit) as exc:
        runpy.run_module(module_name, run_name="__main__")

    assert exc.value.code == 17
    assert calls == [True]
