"""Documented PostgreSQL storage-format constants and stock configuration defaults.

Two kinds of values live here, and only these kinds:

* **Storage-format constants** — fixed by PostgreSQL's on-disk format for the
  standard 8 kB build. Source: PostgreSQL documentation, "Database Page
  Layout" (https://www.postgresql.org/docs/current/storage-page-layout.html)
  and the named source headers where the docs defer to code.
* **Stock GUC defaults** — what a PostgreSQL server does when a setting is not
  configured. Source: https://www.postgresql.org/docs/current/runtime-config.html
  (per-section URLs on each constant). The deployment profile in
  :mod:`capacity_model.resources` overrides these with repo-configured values
  where the repo configures them; otherwise the model *explicitly* falls back
  to these defaults and says so in the report.

Values that are approximations rather than format facts (compression ratios,
average WAL-record overheads) do **not** belong here — they are registered as
:class:`~capacity_model.assumptions.Assumption` objects by the modules that
use them, so the sensitivity pass can perturb them.
"""

from __future__ import annotations

# --- Page / tuple format (storage-page-layout.html; MAXALIGN=8 on x86-64) -----

#: Bytes per page: standard BLCKSZ build.
PAGE_SIZE = 8192
#: PageHeaderData — 24 bytes at the start of every page.
PAGE_HEADER_BYTES = 24
#: ItemIdData line pointer — 4 bytes per tuple on its page.
LINE_POINTER_BYTES = 4
#: HeapTupleHeaderData — 23 bytes, then padded so data starts MAXALIGN-aligned.
HEAP_TUPLE_HEADER_BYTES = 23
#: MAXALIGN on 64-bit platforms.
MAXALIGN = 8
#: IndexTupleData header — 8 bytes (src/include/access/itup.h).
INDEX_TUPLE_HEADER_BYTES = 8
#: B-tree special space per page — BTPageOpaqueData (src/include/access/nbtree.h).
BTREE_SPECIAL_BYTES = 16
#: Default B-tree leaf fillfactor (sql-createindex.html, storage parameter).
BTREE_LEAF_FILLFACTOR = 0.90
#: Default heap fillfactor (sql-createtable.html, storage parameter).
HEAP_FILLFACTOR = 1.00

# --- TOAST (storage-toast.html) -----------------------------------------------

#: Tuples longer than ~2 kB trigger TOASTing ("normally 2 kB").
TOAST_TUPLE_THRESHOLD = 2000
#: Out-of-line chunk payload sized so four chunk rows fit per page.
TOAST_CHUNK_BYTES = 1996
#: Short varlena header (values < 127 bytes); long form is 4 bytes.
VARLENA_SHORT_HEADER = 1
VARLENA_LONG_HEADER = 4

# --- WAL format (wal-internals + src/include/access/xlogrecord.h) -------------

#: WAL segment size for stock builds (wal-configuration.html).
WAL_SEGMENT_BYTES = 16 * 1024 * 1024
#: XLogRecord fixed header (src/include/access/xlogrecord.h).
XLOG_RECORD_HEADER_BYTES = 24

# --- Stock GUC defaults (runtime-config-*.html) --------------------------------

#: runtime-config-connection.html
DEFAULT_MAX_CONNECTIONS = 100
#: runtime-config-resource.html — 128 MB.
DEFAULT_SHARED_BUFFERS_BYTES = 128 * 1024 * 1024
#: runtime-config-resource.html — 4 MB.
DEFAULT_WORK_MEM_BYTES = 4 * 1024 * 1024
#: runtime-config-resource.html — 64 MB.
DEFAULT_MAINTENANCE_WORK_MEM_BYTES = 64 * 1024 * 1024
#: runtime-config-query.html — planner's cache-size hint, 4 GB.
DEFAULT_EFFECTIVE_CACHE_SIZE_BYTES = 4 * 1024 * 1024 * 1024
#: runtime-config-wal.html — checkpoint_timeout, 300 s.
DEFAULT_CHECKPOINT_TIMEOUT_S = 300.0
#: runtime-config-wal.html — max_wal_size, 1 GB.
DEFAULT_MAX_WAL_SIZE_BYTES = 1024 * 1024 * 1024
#: runtime-config-wal.html — checkpoint_completion_target.
DEFAULT_CHECKPOINT_COMPLETION_TARGET = 0.9
#: runtime-config-autovacuum.html — naptime 60 s.
DEFAULT_AUTOVACUUM_NAPTIME_S = 60.0
#: runtime-config-autovacuum.html — autovacuum_vacuum_scale_factor.
DEFAULT_AV_VACUUM_SCALE = 0.20
#: runtime-config-autovacuum.html — autovacuum_vacuum_threshold.
DEFAULT_AV_VACUUM_THRESHOLD = 50
#: runtime-config-autovacuum.html — autovacuum_vacuum_insert_scale_factor (PG13+).
DEFAULT_AV_INSERT_SCALE = 0.20
#: runtime-config-autovacuum.html — autovacuum_analyze_scale_factor.
DEFAULT_AV_ANALYZE_SCALE = 0.10
#: runtime-config-autovacuum.html — autovacuum_max_workers.
DEFAULT_AV_MAX_WORKERS = 3
#: runtime-config-resource.html — vacuum_cost_limit (autovacuum inherits when -1).
DEFAULT_VACUUM_COST_LIMIT = 200
#: runtime-config-autovacuum.html — autovacuum_vacuum_cost_delay, 2 ms.
DEFAULT_AV_COST_DELAY_S = 0.002
#: runtime-config-resource.html — vacuum_cost_page_hit / miss (PG14+) / dirty.
VACUUM_COST_PAGE_HIT = 1
VACUUM_COST_PAGE_MISS = 2
VACUUM_COST_PAGE_DIRTY = 20
#: runtime-config-query.html — default_statistics_target (MCV/histogram size).
DEFAULT_STATISTICS_TARGET = 100
#: runtime-config-query.html — random_page_cost stock default (HDD-era).
DEFAULT_RANDOM_PAGE_COST = 4.0


def maxalign(n: int) -> int:
    """Round ``n`` up to the next MAXALIGN boundary (storage-page-layout.html)."""
    return (n + MAXALIGN - 1) // MAXALIGN * MAXALIGN


def usable_heap_page_bytes(fillfactor: float = HEAP_FILLFACTOR) -> float:
    """Payload bytes available to heap tuples on one page at ``fillfactor``."""
    return (PAGE_SIZE - PAGE_HEADER_BYTES) * fillfactor


def usable_btree_leaf_bytes(fillfactor: float = BTREE_LEAF_FILLFACTOR) -> float:
    """Payload bytes available to index tuples on one B-tree leaf page."""
    return (PAGE_SIZE - PAGE_HEADER_BYTES - BTREE_SPECIAL_BYTES) * fillfactor
