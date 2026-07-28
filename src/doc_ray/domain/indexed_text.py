"""Bound a text value so it can live inside a unique btree index.

PostgreSQL refuses an index entry larger than roughly a third of an 8 KB page::

    index row size 3184 exceeds btree version 4 maximum 2704
    for index "authors_name_key"

Several dimension tables key on unbounded ``text`` under a ``UNIQUE`` constraint
— ``authors.name`` and ``tags.tag`` (migration 004), ``document_collections.name``
(migration 047). Their values come from document metadata and from users, so
neither is length-controlled at the source: a PDF is free to put an entire author
list, or an abstract, in its ``/Author`` field.

The failure lands at the *end* of ingest, when the extracted document is
persisted, so every earlier stage is paid for and then discarded. Bounding
belongs here, next to the invariant it protects, rather than at any one caller.

Truncation on its own would be a data defect of its own making: two different
long names would truncate to the same string and silently merge into one
dimension row. The bound therefore keeps a digest of the *whole* input, so
distinct values stay distinct and equal values stay equal.
"""

from __future__ import annotations

import hashlib

__all__ = [
    "BTREE_MAX_INDEX_ROW_BYTES",
    "INDEX_ROW_OVERHEAD_BYTES",
    "MAX_BTREE_INDEX_BYTES",
    "TRUNCATION_MARK",
    "bound_for_index",
    "check_fits_index",
]

#: PostgreSQL's limit on a whole btree *index row* for the default 8 KB page
#: size, as quoted back by the server in the error above.
BTREE_MAX_INDEX_ROW_BYTES = 2704

#: The index row is the value plus tuple header and alignment padding, so a
#: value of exactly ``BTREE_MAX_INDEX_ROW_BYTES`` still overflows — measured at
#: 2720 for a 2704-byte name. The margin is deliberately far wider than the 16
#: bytes observed: it costs nothing on a name this long, and the exact overhead
#: is an implementation detail of the index type.
INDEX_ROW_OVERHEAD_BYTES = 64

#: The most bytes of *value* this module will hand to a unique btree.
MAX_BTREE_INDEX_BYTES = BTREE_MAX_INDEX_ROW_BYTES - INDEX_ROW_OVERHEAD_BYTES

#: Marks a value the database could not have stored verbatim, so a human reading
#: the row can tell truncation from a genuinely odd name.
TRUNCATION_MARK = "…"

#: Hex characters of SHA-256 kept as the distinguishing suffix. 16 hex chars is
#: 64 bits: collisions between two truncated names are not a practical concern.
_DIGEST_CHARS = 16


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def check_fits_index(value: str, *, label: str, limit: int = MAX_BTREE_INDEX_BYTES) -> str:
    """Return *value*, or raise if it cannot be indexed.

    The counterpart to :func:`bound_for_index`, for values a *person* typed.
    Machine-extracted metadata is bounded silently because rejecting it would
    throw away an otherwise-good ingest; a name someone chose is different —
    storing a truncated version they did not pick is worse than telling them it
    is too long.
    """
    size = len(value.encode("utf-8"))
    if size > limit:
        raise ValueError(f"{label} is too long: {size} bytes, limit {limit}")
    return value


def bound_for_index(value: str, *, limit: int = MAX_BTREE_INDEX_BYTES) -> str:
    """Return *value* if it fits in *limit* bytes, else a bounded stand-in.

    The stand-in is ``<readable head>…<digest>``: the head keeps the row
    recognisable, and the digest of the full original keeps distinct inputs
    distinct and identical inputs identical.

    Byte length is what matters — the btree measures bytes, so a multibyte name
    bounded on character count would still overflow. Truncation is done on the
    decoded string so a codepoint is never split.
    """
    suffix = TRUNCATION_MARK + _digest(value)
    suffix_bytes = len(suffix.encode("utf-8"))
    if limit <= suffix_bytes:
        raise ValueError(
            f"limit {limit} cannot hold the {suffix_bytes}-byte disambiguating suffix; "
            "a bound this small could not keep distinct values distinct"
        )
    if len(value.encode("utf-8")) <= limit:
        return value

    budget = limit - suffix_bytes
    head = value.encode("utf-8")[:budget].decode("utf-8", errors="ignore")
    return head + suffix
