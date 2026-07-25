"""Disposable fixture corpus for the MCP evaluation harness (issue #12, FR-7).

The harness creates and destroys its **own** documents; it must never write to a
corpus it did not create. :func:`assert_safe_test_db` refuses to seed anything but
a dedicated, disposable test database (the same detection ``src/doc_ray/tests/_itdb.py``
uses), and :func:`cleanup_world` removes everything :func:`seed_world` inserted.

Seeding goes through the public persistence adapter (:func:`insert_document`,
:func:`ensure_spans`) plus ``build_sentences`` — the exact inserts ``_itdb`` uses
— so the fixtures are real rows the real read path can serve, not mocks.

This module is the single source of truth for the ordinal landmarks the tasks
assert against (:data:`SPICE_ORDINALS`, :data:`RARE_ORDINALS`, ...): the fixture
text and the task assertions cannot drift because both read these constants.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import psycopg

from doc_ray.adapters import persistence as db
from doc_ray.config import DEFAULT_DATABASE_URL
from doc_ray.domain.sentences import build_sentences
from doc_ray.domain.ulid import new_ulid_str

#: Markers of a dedicated, disposable test database — mirrors ``_itdb`` so the
#: harness recognises the Docker test stack (where other suites' fixtures come and
#: go) and does not mistake their transient documents for a live corpus.
_TEST_DB_PREFIXES = ("docray_test", "pytest_", "test_")
_TEST_HOSTS = {"test-postgres"}
_TEST_PORTS = {15432}

# --- landmarks: the fixture text is generated from these, and the tasks assert
# against them, so the two can never disagree. ----------------------------------
SPICE_ORDINALS = tuple(range(0, 20))  # the flood term, in the first 20 sentences
RARE_ORDINALS = (20, 21)  # the rare, scene-specific term
MAIN_SENTENCE_COUNT = 40
RARE_TERM = "zephyrquux"
FLOOD_TERM = "spice"
ENTITY_SURFACE = "Arrakis"
#: (occurrence index -> NER type) for the type-split entity: LOC dominates (3/7),
#: so typeConsistency ≈ 0.43 — contested, exactly like ``Arrakis`` in the corpus.
ENTITY_TYPES = ("LOC", "LOC", "LOC", "GPE", "GPE", "PERSON", "NORP")
#: Pin every fixture timestamp to a constant so two independent seed+run cycles
#: produce byte-identical payloads (FR-4). ``now()`` defaults would otherwise make
#: ``createdAt`` fields vary in serialized length (fractional seconds) run to run.
_FIXED_TS = "2020-01-01 00:00:00+00"


def _main_sentences() -> str:
    """40 sentences: the flood term in 0–19, the rare term in 20–21, filler after."""
    out: list[str] = []
    for i in range(MAIN_SENTENCE_COUNT):
        if i in RARE_ORDINALS:
            out.append(f"A single {RARE_TERM} bloomed at marker {i}.")
        elif i in SPICE_ORDINALS:
            out.append(f"The {FLOOD_TERM} drifted past marker {i}.")
        else:
            out.append(f"The guild ship passed marker {i}.")
    return " ".join(out)


def _entity_sentences() -> str:
    """Seven sentences, each naming ``Arrakis`` once — one span per mention."""
    verbs = [
        "is the desert",
        "bore the worm",
        "kept the water",
        "knew the storm",
        "fed the guild",
        "named the heir",
        "waited",
    ]
    return " ".join(f"{ENTITY_SURFACE} {v}." for v in verbs)


def _scalar(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> Any:  # noqa: ANN401
    """Return the first column of the single row *sql* yields (asserted present).

    Passes ``None`` (not an empty tuple) when there are no params so psycopg does
    not client-side-parse the query — a literal ``%`` in a ``LIKE`` pattern would
    otherwise be mistaken for a placeholder.
    """
    row = conn.execute(sql, params or None).fetchone()
    assert row is not None, f"expected a row from: {sql}"
    return next(iter(row.values()))


def _looks_like_dedicated_test_database(db_url: str) -> bool:
    parsed = urllib.parse.urlparse(db_url)
    db_name = parsed.path.rsplit("/", 1)[-1]
    return (
        db_name.startswith(_TEST_DB_PREFIXES)
        or (parsed.username or "").endswith("_test")
        or (parsed.hostname or "") in _TEST_HOSTS
        or parsed.port in _TEST_PORTS
    )


def assert_safe_test_db(conn: psycopg.Connection[Any]) -> None:
    """Refuse to seed a database that is not a dedicated, disposable test database.

    Mirrors ``_itdb.require_safe_test_database``: a database that *names itself* a
    test target (host ``test-postgres``, port 15432, a ``docray_test`` name, a
    ``*_test`` role) — or an explicit ``DOCRAY_TEST_ALLOW_LIVE_DB`` override — is
    trusted, so coexisting fixtures from other suites are not mistaken for a live
    corpus. Only an *ambiguous* database is checked for non-fixture documents.
    """
    if str(os.environ.get("DOCRAY_TEST_ALLOW_LIVE_DB", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    if _looks_like_dedicated_test_database(os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)):
        return
    if _scalar(conn, "SELECT to_regclass('public.documents') AS t") is None:
        return
    n = _scalar(
        conn,
        "SELECT count(*) AS n FROM documents "
        "WHERE NOT (title = 'itest' AND coalesce(source_uri, '') LIKE 's3://it%')",
    )
    if int(n):
        raise RuntimeError(
            f"refusing to seed the MCP-eval fixtures into a database with {n} non-fixture "
            "documents; run against the Docker test stack (scripts/test-in-docker.sh)"
        )


def _seed_document(conn: psycopg.Connection[Any], text: str) -> str:
    doc_id = db.insert_document(
        conn,
        title="itest",
        doc_type="txt",
        source_uri="s3://it-mcp-eval",
        content_sha256=new_ulid_str() + new_ulid_str(),
        byte_size=len(text),
        page_count=None,
        full_text=text,
        status="extracted",
        sentences=build_sentences(text),
    )
    # 'english' FTS config (not the degraded 'simple' one) so full_text_search and
    # the compose tools resolve — mirrors an annotated document. Pin the timestamps
    # so the payload is identical across independent seed+run cycles (FR-4).
    conn.execute(
        "UPDATE documents SET language = 'en', created_at = %s, updated_at = %s WHERE id = %s",
        (_FIXED_TS, _FIXED_TS, doc_id),
    )
    return doc_id


def _seed_entities(conn: psycopg.Connection[Any], doc_id: str, text: str) -> None:
    annotation = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool, tool_version) "
        "VALUES (%s, %s, 'entity', 'mcp-eval/fixture', '1')",
        (annotation, doc_id),
    )
    start = 0
    for entity_type in ENTITY_TYPES:
        idx = text.index(ENTITY_SURFACE, start)
        end = idx + len(ENTITY_SURFACE)
        span_id = db.ensure_spans(conn, doc_id, [(idx, end)])[(idx, end)]
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
            "VALUES (%s, %s, %s, %s, %s)",
            (new_ulid_str(), annotation, span_id, entity_type, ENTITY_SURFACE),
        )
        start = end


def seed_world(conn: psycopg.Connection[Any], principal_id: str) -> dict[str, str]:
    """Seed the fixture world and return the ids the tasks address.

    ``principal_id`` is granted the admin role so the harness (reading as that
    principal) can see every fixture regardless of ownership.
    """
    assert_safe_test_db(conn)
    conn.execute(
        "INSERT INTO principals (id, kind, display_name) VALUES (%s, 'service', %s) "
        "ON CONFLICT DO NOTHING",
        (principal_id, f"mcp-eval-{principal_id[:8]}"),
    )
    admin_role = _scalar(conn, "SELECT id FROM roles WHERE name = 'admin'")
    conn.execute(
        "INSERT INTO principal_roles (principal_id, role_id) VALUES (%s, %s) "
        "ON CONFLICT DO NOTHING",
        (principal_id, admin_role),
    )

    main_text = _main_sentences()
    doc_main = _seed_document(conn, main_text)
    entity_text = _entity_sentences()
    doc_entity = _seed_document(conn, entity_text)
    _seed_entities(conn, doc_entity, entity_text)

    collection_id = str(
        _scalar(
            conn,
            "INSERT INTO document_collections (name, description, kind) "
            "VALUES (%s, 'MCP-eval fixture collection', 'manual') RETURNING id",
            (f"mcp-eval-{doc_main[:8]}",),
        )
    )
    job_id = new_ulid_str()
    conn.execute(
        "INSERT INTO jobs (id, kind, status, document_id, created_at) "
        "VALUES (%s, 'ingest', 'succeeded', %s, %s)",
        (job_id, doc_main, _FIXED_TS),
    )
    return {
        "doc_main": doc_main,
        "doc_entity": doc_entity,
        "collection_id": collection_id,
        "job_id": job_id,
    }


def cleanup_world(conn: psycopg.Connection[Any], ids: dict[str, str]) -> None:
    """Destroy everything :func:`seed_world` created (documents cascade)."""
    if ids.get("job_id"):
        conn.execute("DELETE FROM jobs WHERE id = %s", (ids["job_id"],))
    if ids.get("collection_id"):
        conn.execute("DELETE FROM document_collections WHERE id = %s", (ids["collection_id"],))
    for key in ("doc_main", "doc_entity"):
        if ids.get(key):
            conn.execute("DELETE FROM documents WHERE id = %s", (ids[key],))
