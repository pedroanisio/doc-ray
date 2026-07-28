"""The 2026-07-28 ingest failure, reproduced against a real btree.

The domain rule is pinned in `test_indexed_text.py`. This module proves the rule
is applied *where the failure happened* — a unit test on a pure function would
have stayed green throughout the outage.

    index row size 3184 exceeds btree version 4 maximum 2704
    for index "authors_name_key"

The fixture text is deliberately **incompressible**. PostgreSQL compresses an
index entry before measuring it, so a repetitive string of the same length slips
under the limit and does not reproduce the bug at all: `'z' * 3184` inserts
happily. Only high-entropy text of that size actually raises
`ProgramLimitExceeded`, which is why a real author list triggered this and a
naive fixture would not.
"""

from __future__ import annotations

import hashlib
import secrets
import string

import _itdb
import psycopg
import pytest

from doc_ray.adapters.persistence_doc_admin import _author_id, _tag_id

#: The exact size from the production error.
_FAILING_BYTES = 3184
_BTREE_MAX_BYTES = 2704


def _incompressible(size: int, *, seed: str = "") -> str:
    """High-entropy ASCII of *size* bytes; `seed` makes it reproducible per call."""
    if seed:
        out: list[str] = []
        digest = seed.encode("utf-8")
        while len("".join(out)) < size:
            digest = hashlib.sha256(digest).digest()
            out.append(digest.hex())
        return "".join(out)[:size]
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(size))


@pytest.fixture
def conn():
    connection = _itdb.connect()
    try:
        yield connection
    finally:
        connection.close()


def _cleanup(connection, table: str, column: str, values: list[str]) -> None:
    for value in values:
        connection.execute(f"DELETE FROM {table} WHERE {column} = %s", (value,))


# --- the bug itself ----------------------------------------------------------


def test_the_raw_insert_still_proves_the_constraint_is_real(conn):
    """Guard the guard: if this stops raising, every test below is vacuous."""
    name = _incompressible(_FAILING_BYTES)

    with pytest.raises(psycopg.errors.ProgramLimitExceeded):
        conn.execute("INSERT INTO authors (name) VALUES (%s)", (name,))


def test_an_author_name_too_large_for_the_btree_is_accepted(conn):
    """This is the INSERT that aborted the ingest at "Persisting extracted document"."""
    name = "Overlong Author " + _incompressible(_FAILING_BYTES, seed="author-a")

    author_id = _author_id(conn, name)

    stored = conn.execute("SELECT name FROM authors WHERE id = %s", (author_id,)).fetchone()
    assert len(stored["name"].encode("utf-8")) <= _BTREE_MAX_BYTES
    _cleanup(conn, "authors", "name", [stored["name"]])


def test_the_same_overlong_author_upserts_to_one_row(conn):
    """Re-ingesting a document must not create a second author."""
    name = "Repeat Author " + _incompressible(_FAILING_BYTES, seed="author-b")

    first = _author_id(conn, name)
    second = _author_id(conn, name)

    assert first == second
    stored = conn.execute("SELECT name FROM authors WHERE id = %s", (first,)).fetchone()
    _cleanup(conn, "authors", "name", [stored["name"]])


def test_two_different_overlong_authors_stay_two_rows(conn):
    """Truncation without a digest would silently merge these into one author."""
    shared = "Shared Prefix " + _incompressible(_FAILING_BYTES, seed="author-c")

    first_id = _author_id(conn, shared + "ALPHA")
    second_id = _author_id(conn, shared + "BETA")

    assert first_id != second_id
    names = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM authors WHERE id IN (%s, %s)", (first_id, second_id)
        ).fetchall()
    ]
    _cleanup(conn, "authors", "name", names)


def test_an_overlong_tag_is_accepted(conn):
    """`tags.tag` carries the same unbounded UNIQUE text as `authors.name`."""
    tag = "overlong-tag-" + _incompressible(_FAILING_BYTES, seed="tag-a")

    tag_id = _tag_id(conn, tag)

    stored = conn.execute("SELECT tag FROM tags WHERE id = %s", (tag_id,)).fetchone()
    assert len(stored["tag"].encode("utf-8")) <= _BTREE_MAX_BYTES
    _cleanup(conn, "tags", "tag", [stored["tag"]])


# --- the fix must not disturb ordinary data ----------------------------------


def test_an_ordinary_author_name_is_stored_verbatim(conn):
    name = "Ada Lovelace (indexed-text regression)"

    author_id = _author_id(conn, name)

    stored = conn.execute("SELECT name FROM authors WHERE id = %s", (author_id,)).fetchone()
    assert stored["name"] == name
    _cleanup(conn, "authors", "name", [name])


def test_a_multibyte_author_name_over_the_byte_budget_is_accepted(conn):
    """Bounding on characters instead of bytes would let this through oversized."""
    name = "".join(chr(0x4E00 + (i % 20000)) for i in range(1500))  # ~4500 bytes

    author_id = _author_id(conn, name)

    stored = conn.execute("SELECT name FROM authors WHERE id = %s", (author_id,)).fetchone()
    assert len(stored["name"].encode("utf-8")) <= _BTREE_MAX_BYTES
    stored["name"].encode("utf-8").decode("utf-8")  # not split mid-codepoint
    _cleanup(conn, "authors", "name", [stored["name"]])
