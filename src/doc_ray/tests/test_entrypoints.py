"""CLI entrypoints: ingest cli, migration runner, and reextract (with reannotate)."""

from __future__ import annotations

import _itdb

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
