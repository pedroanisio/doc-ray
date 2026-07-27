"""Static schema/store guards for durable exact-surface NER rules."""

from __future__ import annotations

from pathlib import Path

from doc_ray.adapters import ner_corrections_store as store

_ROOT = Path(__file__).resolve().parents[3]
_FEATURE = _ROOT / "db" / "migrations" / "140_entity_surface_corrections.sql"
_VERSION = _ROOT / "db" / "migrations" / "141_schema_version_1_51_0.sql"


def test_feature_migration_encodes_both_rule_shapes_without_sentinels():
    sql = _FEATURE.read_text(encoding="utf-8").lower()
    assert "reclassify_surface" in sql and "reject_surface" in sql
    assert "surface_original_type" in sql
    assert "alter column char_start drop not null" in sql
    assert "alter column char_end drop not null" in sql
    assert "char_start is null" in sql and "char_end is null" in sql
    assert "char_start is not null" in sql and "char_end is not null" in sql
    assert "-1" not in sql
    assert "140_entity_surface_corrections.sql" in sql


def test_store_maps_the_surface_matcher_on_every_read_and_write_path():
    assert "surface_original_type" in store._LOAD_CORRECTIONS
    assert "surface_original_type" in store._LIST_CORRECTION_ROWS
    assert "surface_original_type" in store._INSERT_CORRECTION


def test_version_migration_advances_the_schema_to_1_51_0():
    sql = _VERSION.read_text(encoding="utf-8")
    assert "1.51.0" in sql
    assert "141_schema_version_1_51_0.sql" in sql
