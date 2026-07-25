"""Tests for the capacity model's storage-format and queueing primitives.

These pin the pure-math layers to hand-computed values from the documented
PostgreSQL page layout and standard M/M/c results, so the doc-ray-specific
layers above them can be trusted to be arithmetic over audited primitives.
"""

from __future__ import annotations

import math

import pytest
from capacity_model import pg_constants as pgc
from capacity_model.assumptions import AssumptionRegistry, Epistemic, Source
from capacity_model.queueing import concurrency, erlang_c, mmc_station
from capacity_model.storage import (
    Column,
    btree_index_bytes,
    heap_table_bytes,
    heap_tuple_bytes,
)


class TestPageMath:
    """Alignment and page-capacity arithmetic."""

    def test_maxalign_rounds_up_to_8(self) -> None:
        """23-byte heap header pads to 24; exact multiples stay put."""
        assert pgc.maxalign(23) == 24
        assert pgc.maxalign(24) == 24
        assert pgc.maxalign(1) == 8

    def test_usable_heap_page(self) -> None:
        """A default-fillfactor heap page offers 8192 − 24 payload bytes."""
        assert pgc.usable_heap_page_bytes() == 8168


class TestHeapSizing:
    """Row and table size estimates against hand-computed layouts."""

    def test_single_bigint_row(self) -> None:
        """One NOT NULL bigint: 24-byte header + 8 data = 32 bytes, no TOAST."""
        tup, toast = heap_tuple_bytes([Column("id", "bigint")])
        assert tup == 32
        assert toast == 0

    def test_null_bitmap_added_when_nullable(self) -> None:
        """A nullable column forces a bitmap byte inside the padded header."""
        tup, _ = heap_tuple_bytes([Column("id", "bigint", null_frac=0.5)])
        assert tup == 32  # ceil(1/8)=1 bitmap byte still fits in maxalign(23+1)=24

    def test_short_varlena(self) -> None:
        """A 10-byte text value stores as 1-byte header + payload."""
        col = Column("t", "text", avg_bytes=10)
        in_row, toast = col.stored_bytes()
        assert in_row == 11
        assert toast == 0

    def test_toasted_value_leaves_18_byte_pointer(self) -> None:
        """A 5000-byte text value goes out-of-line in ~2000-byte chunk rows."""
        col = Column("t", "text", avg_bytes=5000)
        in_row, toast = col.stored_bytes()
        assert in_row == 18
        assert toast == math.ceil(5000 / pgc.TOAST_CHUNK_BYTES) * (pgc.PAGE_SIZE / 4)

    def test_rows_pack_per_page(self) -> None:
        """226 rows of (32+4) bytes fit one 8168-byte page exactly."""
        heap, _ = heap_table_bytes(226, [Column("id", "bigint")])
        assert heap == pgc.PAGE_SIZE
        heap2, _ = heap_table_bytes(227, [Column("id", "bigint")])
        assert heap2 == 2 * pgc.PAGE_SIZE


class TestBtreeSizing:
    """B-tree estimates against the known size of a bigint index."""

    def test_million_row_bigint_index(self) -> None:
        """1M bigint keys ≈ 21–24 MB and height 3 — matches real-world size."""
        est = btree_index_bytes(1_000_000, key_bytes=8)
        assert est.entries_per_leaf == 366
        assert 20e6 < est.total_bytes < 25e6
        assert est.height == 3

    def test_dedup_factor_shrinks_leaves(self) -> None:
        """Deduplication factor scales leaf count roughly linearly."""
        full = btree_index_bytes(1_000_000, key_bytes=8)
        deduped = btree_index_bytes(1_000_000, key_bytes=8, dedup_factor=0.5)
        assert deduped.leaf_pages == pytest.approx(full.leaf_pages / 2, rel=0.01)


class TestQueueing:
    """M/M/c formulas against closed-form M/M/1 special cases."""

    def test_erlang_c_reduces_to_rho_for_single_server(self) -> None:
        """For c=1, P(wait) = ρ."""
        assert erlang_c(1, 0.5) == pytest.approx(0.5)
        assert erlang_c(1, 0.9) == pytest.approx(0.9)

    def test_mm1_mean_response(self) -> None:
        """M/M/1: W = s / (1 − ρ)."""
        st = mmc_station("cpu", arrival_rate=50.0, service_s=0.01, servers=1)
        assert st.utilization == pytest.approx(0.5)
        assert st.mean_response_s == pytest.approx(0.01 / (1 - 0.5))

    def test_saturation_reports_infinite_latency(self) -> None:
        """ρ ≥ 1 must flag saturation, not return garbage."""
        st = mmc_station("disk", arrival_rate=200.0, service_s=0.01, servers=1)
        assert st.saturated
        assert st.mean_response_s == float("inf")

    def test_percentiles_ordered(self) -> None:
        """p99 ≥ p95 ≥ mean service time, always."""
        st = mmc_station("cpu", arrival_rate=700.0, service_s=0.01, servers=8)
        assert st.p99_response_s >= st.p95_response_s >= st.service_s

    def test_littles_law(self) -> None:
        """L = λ·W."""
        assert concurrency(100.0, 0.05) == pytest.approx(5.0)


class TestAssumptionRegistry:
    """Registry semantics: no silent redefinition, auditable overrides."""

    def test_conflicting_redefinition_raises(self) -> None:
        """The same name with a different value is a modeling bug."""
        reg = AssumptionRegistry()
        reg.define("x", 1.0, "u", Source.ASSUMED, "c", "r")
        with pytest.raises(ValueError):
            reg.define("x", 2.0, "u", Source.ASSUMED, "c", "r")

    def test_override_marks_provenance(self) -> None:
        """User overrides are recorded as ASSUMED/EMPIRICAL with the old value."""
        reg = AssumptionRegistry()
        reg.define("x", 1.0, "u", Source.PG_DOCS, "url", "r")
        reg.override("x", 3.0)
        entry = reg.entries["x"]
        assert entry.value == 3.0
        assert entry.source is Source.ASSUMED
        assert entry.epistemic is Epistemic.EMPIRICAL
        assert "was 1.0" in entry.citation

    def test_override_unknown_name_raises(self) -> None:
        """Overrides must target declared inputs — no hidden knobs."""
        reg = AssumptionRegistry()
        with pytest.raises(KeyError):
            reg.override("nope", 1.0)
