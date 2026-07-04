"""Unit tests for db_analyzer orchestration and CLI behavior."""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
import pytest

from doc_ray.application.db_analyzer import run
from doc_ray.application.db_analyzer.config import AnalyzerConfig
from doc_ray.application.db_analyzer.engines import registry
from doc_ray.application.db_analyzer.engines.postgres import PostgresEngine
from doc_ray.application.db_analyzer.models import (
    AnalysisResult,
    ColumnInfo,
    Issue,
    TableProfile,
    TableStructure,
)


def _structure(schema="public", name="documents"):
    return TableStructure(
        schema=schema,
        name=name,
        columns=[ColumnInfo("id", "integer", False, None, 1)],
        primary_key=["id"],
        foreign_keys=[],
        indexes=[],
        constraints=[],
        size_bytes=1024,
        seq_scan=0,
        idx_scan=1,
    )


class _FakeEngine:
    def __init__(self):
        self.connections = []
        self.profile_calls = []

    @contextmanager
    def connect(self, dsn, *, read_only):
        conn = object()
        self.connections.append((dsn, read_only))
        yield conn

    def database_name(self, conn):
        return "fake_db"

    def list_schemas(self, conn):
        return ["public", "internal", "skip_me"]

    def list_tables(self, conn, schema):
        return {"public": ["documents"], "internal": ["audit"]}.get(schema, [])

    def get_table_structure(self, conn, schema, table):
        return _structure(schema, table)

    def profile_table(
        self,
        conn,
        schema,
        table,
        columns,
        primary_key,
        *,
        sample_threshold,
        duplicate_threshold,
    ):
        self.profile_calls.append(
            (schema, table, columns, primary_key, sample_threshold, duplicate_threshold)
        )
        return TableProfile(schema=schema, name=table, row_count=1, columns=[])


def test_analyze_filters_schemas_and_profiles_tables(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(run, "get_engine", lambda name: engine)

    result = run.analyze(
        AnalyzerConfig(
            engine="fake",
            dsn="postgresql://example/db",
            include_schemas=("public", "internal"),
            exclude_schemas=("internal",),
            sample_threshold=11,
            duplicate_threshold=22,
        )
    )

    assert result.database == "fake_db"
    assert result.schemas == ["public"]
    assert [t.name for t in result.tables] == ["documents"]
    assert [p.name for p in result.profiles] == ["documents"]
    assert engine.connections == [("postgresql://example/db", True)]
    assert engine.profile_calls[0][4:] == (11, 22)


def test_analyze_structure_only_skips_profiles(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(run, "get_engine", lambda name: engine)

    result = run.analyze(AnalyzerConfig(engine="fake", structure_only=True))

    assert result.tables
    assert result.profiles == []
    assert engine.profile_calls == []


def test_main_writes_reports_and_returns_issue_exit_code(monkeypatch, tmp_path, capsys):
    issue = Issue("missing-primary-key", "warning", "public.docs", "missing pk", "quality")
    result = AnalysisResult(
        engine="fake",
        database="fake_db",
        schemas=["public"],
        tables=[_structure()],
        profiles=[],
        issues=[issue],
    )
    monkeypatch.setattr(run, "analyze", lambda cfg: result)

    code = run.main(["--dsn", "x", "--output-dir", str(tmp_path), "--json"])

    captured = capsys.readouterr()
    assert code == 2
    assert '"database": "fake_db"' in captured.out
    assert (tmp_path / "db_analysis.md").exists()
    assert (tmp_path / "db_analysis.json").exists()


def test_main_reports_configuration_errors(tmp_path, capsys):
    missing = tmp_path / "missing.json"

    assert run.main(["--config", str(missing)]) == 1

    assert "Invalid configuration:" in capsys.readouterr().err


def test_main_reports_unknown_engine(monkeypatch, capsys):
    monkeypatch.setattr(run, "analyze", lambda cfg: (_ for _ in ()).throw(ValueError("bad engine")))

    assert run.main([]) == 1

    assert "bad engine" in capsys.readouterr().err


def test_main_reports_unreachable_database(monkeypatch, capsys):
    monkeypatch.setattr(
        run,
        "analyze",
        lambda cfg: (_ for _ in ()).throw(psycopg.OperationalError("connection failed")),
    )

    assert run.main([]) == 1

    assert "Cannot reach database:" in capsys.readouterr().err


def test_engine_registry_resolves_aliases_and_reports_unknown_engines():
    assert isinstance(registry.get_engine("postgres"), PostgresEngine)
    assert isinstance(registry.get_engine(" POSTGRESQL "), PostgresEngine)

    with pytest.raises(ValueError, match="unknown database engine"):
        registry.get_engine("sqlite")
