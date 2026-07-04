"""Unit tests for db_analyzer configuration loading and precedence."""

from __future__ import annotations

import json

import pytest

from doc_ray.application.db_analyzer.config import AnalyzerConfig, load_config


def test_defaults_when_nothing_provided():
    assert load_config(env={}) == AnalyzerConfig()


def test_database_url_env_var_sets_dsn():
    cfg = load_config(env={"DATABASE_URL": "postgresql://x/y"})
    assert cfg.dsn == "postgresql://x/y"


def test_db_analyzer_dsn_wins_over_database_url():
    cfg = load_config(
        env={"DATABASE_URL": "postgresql://x/y", "DB_ANALYZER_DSN": "postgresql://z/w"}
    )
    assert cfg.dsn == "postgresql://z/w"


def test_config_file_is_applied(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"sample_threshold": 5, "include_schemas": ["public", "app"]}))
    cfg = load_config(env={}, config_path=str(path))
    assert cfg.sample_threshold == 5
    assert cfg.include_schemas == ("public", "app")


def test_env_wins_over_config_file(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"sample_threshold": 5}))
    cfg = load_config(env={"DB_ANALYZER_SAMPLE_THRESHOLD": "9"}, config_path=str(path))
    assert cfg.sample_threshold == 9


def test_overrides_win_over_everything(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"sample_threshold": 5}))
    cfg = load_config(
        env={"DB_ANALYZER_SAMPLE_THRESHOLD": "9"},
        config_path=str(path),
        overrides={"sample_threshold": 42},
    )
    assert cfg.sample_threshold == 42


def test_overrides_with_none_values_are_ignored():
    cfg = load_config(env={}, overrides={"sample_threshold": None, "engine": "postgres"})
    assert cfg.sample_threshold == AnalyzerConfig().sample_threshold
    assert cfg.engine == "postgres"


def test_csv_env_vars_split_and_trim():
    cfg = load_config(env={"DB_ANALYZER_SCHEMAS": "public, app , "})
    assert cfg.include_schemas == ("public", "app")


def test_read_only_env_var_parses_falsey_values():
    assert load_config(env={"DB_ANALYZER_READ_ONLY": "false"}).read_only is False
    assert load_config(env={}).read_only is True


def test_structure_only_env_var_parses_truthy_values():
    assert load_config(env={"DB_ANALYZER_STRUCTURE_ONLY": "yes"}).structure_only is True


def test_config_file_must_be_a_json_object(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError):
        load_config(env={}, config_path=str(path))


def test_config_path_falls_back_to_env_var(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"sample_threshold": 7}))
    cfg = load_config(env={"DB_ANALYZER_CONFIG": str(path)})
    assert cfg.sample_threshold == 7
