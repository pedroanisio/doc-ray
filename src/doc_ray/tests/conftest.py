"""Make the `ingest` package importable for these tests regardless of cwd.

Only imports the dependency-free modules (ulid, sentences); it never imports
ingest.db/storage/pipeline, so psycopg/boto3 are not required to run this suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


@pytest.fixture
def pg():
    """A live DB connection + a doc-id cleanup registry for integration tests.

    Yields (conn, created): append document ids to `created` and they are
    deleted (cascade) at teardown. Skips cleanly if the DB is unreachable.
    """
    import _itdb
    try:
        conn = _itdb.connect()
    except Exception as e:                       # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {e}")
    created: list[str] = []
    try:
        yield conn, created
    finally:
        for doc_id in created:
            try:
                conn.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
            except Exception:
                pass
        conn.close()
