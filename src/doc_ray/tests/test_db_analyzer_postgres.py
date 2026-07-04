"""Integration tests for PostgresEngine against the live doc-ray database."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from doc_ray.application.db_analyzer.engines.postgres import PostgresEngine


@pytest.fixture
def scratch_tables(pg):
    """Two throwaway tables (parent + FK child) dropped at teardown."""
    conn, _created = pg
    suffix = uuid.uuid4().hex[:8]
    parent = f"db_analyzer_scratch_parent_{suffix}"
    child = f"db_analyzer_scratch_child_{suffix}"
    conn.execute(f"CREATE TABLE {parent} (id serial PRIMARY KEY, name text)")
    conn.execute(
        f"""
        CREATE TABLE {child} (
            id serial PRIMARY KEY,
            parent_id integer REFERENCES {parent}(id),
            label text,
            amount integer,
            created_at date
        )
        """
    )
    conn.execute(
        f"INSERT INTO {parent} (name) VALUES ('p1')"
    )
    conn.execute(
        f"INSERT INTO {child} (parent_id, label, amount, created_at) VALUES "
        "(1, 'a', 10, '2024-01-01'), (1, 'b', 20, '2024-01-02'), "
        "(1, NULL, 1000, '2024-01-03'), (1, 'a', 10, '2024-01-01')"
    )
    try:
        yield parent, child
    finally:
        conn.execute(f"DROP TABLE IF EXISTS {child}")
        conn.execute(f"DROP TABLE IF EXISTS {parent}")


def test_list_schemas_includes_public(pg):
    conn, _ = pg
    assert "public" in PostgresEngine().list_schemas(conn)


def test_get_table_structure_reports_pk_and_columns(scratch_tables, pg):
    conn, _ = pg
    _parent, child = scratch_tables
    structure = PostgresEngine().get_table_structure(conn, "public", child)
    assert structure.primary_key == ["id"]
    assert {"id", "parent_id", "label", "amount", "created_at"} <= {c.name for c in structure.columns}
    assert structure.size_bytes > 0


def test_get_table_structure_reports_foreign_key(scratch_tables, pg):
    conn, _ = pg
    parent, child = scratch_tables
    structure = PostgresEngine().get_table_structure(conn, "public", child)
    assert len(structure.foreign_keys) == 1
    fk = structure.foreign_keys[0]
    assert fk.columns == ["parent_id"]
    assert fk.ref_table == parent
    assert fk.ref_columns == ["id"]


def test_profile_table_computes_null_numeric_and_duplicate_stats(scratch_tables, pg):
    conn, _ = pg
    _parent, child = scratch_tables
    engine = PostgresEngine()
    structure = engine.get_table_structure(conn, "public", child)
    profile = engine.profile_table(
        conn,
        "public",
        child,
        structure.columns,
        structure.primary_key,
        sample_threshold=100_000,
        duplicate_threshold=100_000,
    )
    assert profile.row_count == 4

    label_col = next(c for c in profile.columns if c.name == "label")
    assert label_col.null_count == 1
    assert label_col.null_pct == 25.0

    amount_col = next(c for c in profile.columns if c.name == "amount")
    assert amount_col.min_value == 10
    assert amount_col.max_value == 1000

    date_col = next(c for c in profile.columns if c.name == "created_at")
    assert date_col.min_value is not None and date_col.max_value is not None

    # ('1', 'a', 10, '2024-01-01') appears twice among the non-PK columns' full row.
    assert profile.duplicate_group_count == 1


def test_read_only_connection_rejects_writes(pg):
    import _itdb

    with PostgresEngine().connect(_itdb.DB_URL, read_only=True) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("CREATE TABLE db_analyzer_should_never_exist (id int)")
