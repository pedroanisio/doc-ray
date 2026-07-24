"""Make the `ingest` package importable for these tests regardless of cwd.

Only imports the dependency-free modules (ulid, sentences); it never imports
ingest.db/storage/pipeline, so psycopg/boto3 are not required to run this suite.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def pytest_sessionstart(session):
    """Refuse DB integration tests when DATABASE_URL points at live corpus data."""
    import _itdb

    try:
        conn = _itdb.connect()
    except _itdb.UnsafeTestDatabaseError as exc:
        raise pytest.UsageError(str(exc)) from exc
    except Exception:  # pragma: no cover - env dependent
        return
    conn.close()


@pytest.fixture()
def qconn():
    """A connection whose ``jobs`` table is a private temp shadow.

    Shared by the job-dispatch/worker test modules (test_jobs_worker*.py,
    D-015 split): the session-local TEMP table shadows ``public.jobs`` via
    search-path precedence, so nothing here can claim, complete, or fail
    another process's real queued work.
    """
    import _itdb

    migrations = Path(__file__).resolve().parents[3] / "db" / "migrations"
    conn = _itdb.connect()
    conn.execute((migrations / "059_job_progress.sql").read_text())
    conn.execute((migrations / "072_jobs_drain_requested_at.sql").read_text())
    conn.execute("CREATE TEMP TABLE jobs (LIKE public.jobs INCLUDING ALL)")
    conn.execute("GRANT ALL PRIVILEGES ON jobs TO docray_batch")
    yield conn
    conn.close()


@pytest.fixture
def pg():
    """A live DB connection + a doc-id cleanup registry for integration tests.

    Yields (conn, created): append document ids to `created` and they are
    deleted (cascade) at teardown. Skips cleanly if the DB is unreachable.
    """
    import _itdb

    try:
        conn = _itdb.connect()
    except _itdb.UnsafeTestDatabaseError:
        raise
    except Exception as e:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {e}")
    created: list[str] = []
    try:
        yield conn, created
    finally:
        for doc_id in created:
            with contextlib.suppress(Exception):
                conn.execute("DELETE FROM documents WHERE id=%s", (doc_id,))
        conn.close()
