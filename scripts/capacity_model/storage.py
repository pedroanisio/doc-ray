"""Heap, B-tree, GIN, HNSW, and TOAST size estimators from the on-disk format.

All arithmetic follows the documented page layout (see
:mod:`capacity_model.pg_constants`). Estimators here are *pure math over
explicit parameters* — the doc-ray-specific column shapes and cardinalities
live in :mod:`capacity_model.schema_catalog`, and fuzzy coefficients
(compression ratios, posting-list bytes) are registered as assumptions by
:mod:`capacity_model.sizing` so they stay visible and perturbable.

References:

* Page/tuple layout: https://www.postgresql.org/docs/current/storage-page-layout.html
* TOAST (thresholds, 18-byte pointer datum, ~2000-byte chunks):
  https://www.postgresql.org/docs/current/storage-toast.html
* B-tree structure and fillfactor:
  https://www.postgresql.org/docs/current/btree-implementation.html and
  https://www.postgresql.org/docs/current/sql-createindex.html
* GIN internals (entry tree + compressed posting lists):
  https://www.postgresql.org/docs/current/gin.html
* pgvector storage (``4 * dimensions + 8`` bytes per vector):
  https://github.com/pgvector/pgvector#storage
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from capacity_model import pg_constants as pgc

#: Fixed-width type name → (bytes, alignment). Sources: PostgreSQL docs,
#: chapter "Data Types" (per-type storage-size tables); uuid/enum alignment
#: from ``pg_type.typalign``.
FIXED_TYPES: dict[str, tuple[int, int]] = {
    "smallint": (2, 2),
    "integer": (4, 4),
    "bigint": (8, 8),
    "real": (4, 4),
    "double precision": (8, 8),
    "boolean": (1, 1),
    "uuid": (16, 1),
    "timestamptz": (8, 8),
    "timestamp": (8, 8),
    "date": (4, 4),
    "enum": (4, 4),
    "oid": (4, 4),
}

#: TOAST pointer datum: 18 bytes on disk including its varlena header
#: (storage-toast.html: "the total size of an on-disk TOAST pointer datum is
#: therefore 18 bytes").
TOAST_POINTER_BYTES = 18


@dataclass(frozen=True)
class Column:
    """One column: a fixed-width type name, or a varlena with an average size.

    ``avg_bytes`` is the average *stored* (post-compression) payload for
    varlena types (text/jsonb/bytea/tsvector/numeric/vector); ignored for
    fixed-width types. ``null_frac`` discounts storage for mostly-NULL columns.
    """

    name: str
    typ: str
    avg_bytes: float = 0.0
    null_frac: float = 0.0

    def stored_bytes(self) -> tuple[float, float]:
        """Return ``(in_row_bytes, toast_bytes)`` for the average value.

        Varlena values whose average exceeds the TOAST threshold are modeled
        out-of-line: an 18-byte pointer stays in the row and the payload goes
        to the TOAST relation in ~2000-byte chunk rows (each chunk row pays
        its own tuple overhead).
        """
        if self.typ in FIXED_TYPES:
            size, align = FIXED_TYPES[self.typ]
            in_row = float(_align(size, align))
        elif self.avg_bytes > pgc.TOAST_TUPLE_THRESHOLD:
            n_chunks = math.ceil(self.avg_bytes / pgc.TOAST_CHUNK_BYTES)
            # Each TOAST chunk row: header + (chunk_id int4, chunk_seq int4,
            # chunk_data varlena) + line pointer, packed 4 per page by design.
            toast = n_chunks * (pgc.PAGE_SIZE / 4.0)
            return (
                TOAST_POINTER_BYTES * (1.0 - self.null_frac),
                toast * (1.0 - self.null_frac),
            )
        elif self.avg_bytes < 127:
            in_row = pgc.VARLENA_SHORT_HEADER + self.avg_bytes
        else:
            in_row = float(_align(int(pgc.VARLENA_LONG_HEADER + self.avg_bytes), 4))
        return (in_row * (1.0 - self.null_frac), 0.0)


def _align(n: int, alignment: int) -> int:
    """Round ``n`` up to a multiple of ``alignment``."""
    return (n + alignment - 1) // alignment * alignment


def heap_tuple_bytes(columns: list[Column]) -> tuple[float, float]:
    """Average ``(heap_tuple_bytes, toast_bytes)`` for one row of ``columns``.

    Tuple = MAXALIGN(23-byte header + null bitmap if any column is nullable)
    + aligned column payloads, MAXALIGNed as a whole (storage-page-layout.html).
    """
    has_nulls = any(c.null_frac > 0 for c in columns)
    bitmap = math.ceil(len(columns) / 8) if has_nulls else 0
    header = pgc.maxalign(pgc.HEAP_TUPLE_HEADER_BYTES + bitmap)
    data = 0.0
    toast = 0.0
    for col in columns:
        in_row, out = col.stored_bytes()
        data += in_row
        toast += out
    return (header + pgc.maxalign(math.ceil(data)), toast)


def heap_table_bytes(row_count: float, columns: list[Column]) -> tuple[float, float]:
    """Return ``(heap_bytes, toast_bytes)`` for ``row_count`` rows.

    Rows are packed per page: floor(usable / (tuple + line pointer)) rows per
    page. FSM/VM forks add ~0.1% and are folded into the page rounding.
    """
    tuple_bytes, toast_bytes = heap_tuple_bytes(columns)
    per_row = tuple_bytes + pgc.LINE_POINTER_BYTES
    rows_per_page = max(1, int(pgc.usable_heap_page_bytes() // per_row))
    pages = math.ceil(row_count / rows_per_page)
    return (pages * float(pgc.PAGE_SIZE), toast_bytes * row_count)


@dataclass(frozen=True)
class BtreeEstimate:
    """Size and shape of a B-tree index."""

    total_bytes: float
    leaf_pages: float
    height: int
    entries_per_leaf: int


def btree_index_bytes(
    row_count: float, key_bytes: float, dedup_factor: float = 1.0
) -> BtreeEstimate:
    """Estimate a B-tree index: leaf pages, upper levels, and height.

    Leaf entry = MAXALIGN(8-byte IndexTuple header + key) + 4-byte line
    pointer, packed at the default leaf fillfactor of 90%
    (btree-implementation.html, sql-createindex.html). ``dedup_factor`` < 1
    models PostgreSQL 13+ B-tree deduplication for low-cardinality keys.
    Upper levels are geometric with the leaf fanout; height counts root→leaf
    page reads (the buffer touches an index lookup costs).
    """
    entry = pgc.maxalign(pgc.INDEX_TUPLE_HEADER_BYTES + math.ceil(key_bytes))
    per_entry = entry + pgc.LINE_POINTER_BYTES
    entries_per_leaf = max(2, int(pgc.usable_btree_leaf_bytes() // per_entry))
    leaf_pages = math.ceil(row_count * dedup_factor / entries_per_leaf)
    fanout = float(entries_per_leaf)
    upper = 0.0
    level_pages = float(leaf_pages)
    height = 1
    while level_pages > 1:
        level_pages = math.ceil(level_pages / fanout)
        upper += level_pages
        height += 1
    total_pages = leaf_pages + max(upper, 1.0)
    return BtreeEstimate(
        total_bytes=total_pages * float(pgc.PAGE_SIZE),
        leaf_pages=float(leaf_pages),
        height=height,
        entries_per_leaf=entries_per_leaf,
    )


def gin_index_bytes(
    posting_count: float,
    distinct_keys: float,
    avg_key_bytes: float,
    bytes_per_posting: float,
    page_utilization: float,
) -> float:
    """Estimate a GIN index (e.g. on a ``tsvector`` column).

    GIN stores a B-tree of distinct keys plus compressed posting lists/trees
    of item pointers (gin.html: "posting list" / "posting tree", varbyte-
    encoded item pointers since 9.4). ``bytes_per_posting`` and
    ``page_utilization`` are caller-registered assumptions — compression
    depends on data, so they are explicitly empirical.
    """
    entry_tree = distinct_keys * (
        pgc.maxalign(pgc.INDEX_TUPLE_HEADER_BYTES + math.ceil(avg_key_bytes))
        + pgc.LINE_POINTER_BYTES
    )
    postings = posting_count * bytes_per_posting
    return (entry_tree + postings) / page_utilization


def hnsw_index_bytes(row_count: float, dimensions: int, m_links: int) -> float:
    """Estimate a pgvector HNSW index.

    Per element: the vector itself (``4 * dims + 8`` bytes,
    pgvector README "Storage") plus ~``2 * m`` graph links of 8 bytes across
    layers (HNSW paper: Malkov & Yashunin 2018, arXiv:1603.09320 — each node
    keeps M links, doubled for layer 0), plus tuple overhead per element.
    """
    vector_bytes = 4.0 * dimensions + 8.0
    link_bytes = 2.0 * m_links * 8.0
    per_element = vector_bytes + link_bytes + pgc.INDEX_TUPLE_HEADER_BYTES + pgc.LINE_POINTER_BYTES
    return row_count * per_element / 0.9
