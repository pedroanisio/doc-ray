"""Backup & restore — a full, self-contained snapshot of the doc-ray corpus.

A backup archive is a single ``.tar.gz`` bundling two halves:

* **database** — a binary ``COPY`` dump of every public base table (except the
  operational ``jobs`` queue and the ``backups`` catalog itself), plus every
  sequence's value. Binary COPY preserves ULIDs, ``tsvector``, ``vector(768)``,
  arrays and jsonb exactly.
* **blobs** — the original document bytes from the object store (everything
  except prior backup archives).

The archive is uploaded to the object store under ``backups/<id>.tar.gz`` and
indexed in the ``backups`` table (migration 026).

Restore contract: the target database must already be at the **same schema
version** (run migrations first). Restore then rewrites all corpus data —
``DELETE`` every covered table and ``COPY`` the snapshot back — inside one
transaction with ``session_replication_role = replica`` so foreign-key and
trigger enforcement is deferred during the bulk load (the language-aware ``fts``
trigger stays quiet, preserving the dumped tsvectors). Blobs are re-uploaded.

Requires a superuser connection (``session_replication_role`` is SUSET); the
worker's connection is the trusted superuser role, so KIND_RESTORE runs there.
"""

from __future__ import annotations

import io
import json
import tarfile
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.rows import tuple_row

from doc_ray.application.integrity import sha256_hex, verify_bytes
from doc_ray.domain.progress import report_progress
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.object_store import ObjectStore


def _q(
    conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    """Run a read and return tuples, regardless of the connection's row factory."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _q1(
    conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()
) -> tuple[Any, ...] | None:
    rows = _q(conn, sql, params)
    return rows[0] if rows else None


def _scalar(conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] = ()) -> Any:  # noqa: ANN401
    """Return the first column of the first row (for count(*)/setval-style reads).

    Intentionally ``Any`` — the value's type depends on the query (int for
    ``count(*)``, str for a version string, ...); callers narrow it.
    """
    row = _q1(conn, sql, params)
    assert row is not None  # count()/scalar aggregate always yields exactly one row
    return row[0]


#: Base tables never captured: the live queue and the backup catalog itself.
EXCLUDED_TABLES = frozenset({"jobs", "backups"})
#: Object keys under this prefix are backup archives — never backed up.
_BACKUP_PREFIX = "backups/"
_ARCHIVE_VERSION = 1


def _covered_tables(conn: psycopg.Connection[Any]) -> list[str]:
    """Return the public base tables included in a backup (sorted, minus excludes)."""
    rows = _q(
        conn,
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname",
    )
    return [r[0] for r in rows if r[0] not in EXCLUDED_TABLES]


def unreadable_tables(
    conn: psycopg.Connection[Any], tables: list[str], *, role: str = "docray_batch"
) -> list[str]:
    """Return the covered tables *role* cannot ``SELECT`` — the backup would fail on these.

    A backup COPYs every covered table elevated to the trusted writer
    ``docray_batch``. A table that role cannot read aborts the whole COPY with a
    raw ``permission denied`` and a half-written archive (production, 2026-07-24:
    ``web_auth_sessions`` had no grant because migration 106's one-time
    ``GRANT ALL`` never covered a table added later). Checking up front turns that
    into a clear, early failure that names the offending tables. Returns them
    sorted; empty means every table is readable.
    """
    if not tables:
        return []
    rows = _q(
        conn,
        "SELECT t AS name, "
        "  has_table_privilege(%s, ('public.' || quote_ident(t))::regclass, 'SELECT') AS ok "
        "FROM unnest(%s::text[]) AS t",
        (role, tables),
    )
    return sorted(r[0] for r in rows if not r[1])


def _columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    """Return a table's non-generated column names, in ordinal order.

    Generated columns are excluded — they cannot be COPYed into and recompute
    themselves on restore.
    """
    rows = _q(
        conn,
        "SELECT attname FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped "
        "  AND attgenerated = '' ORDER BY attnum",
        (f'public."{table}"',),
    )
    return [r[0] for r in rows]


def _col_list(cols: list[str]) -> str:
    return ", ".join(f'"{c}"' for c in cols)


def _schema_version(conn: psycopg.Connection[Any]) -> str | None:
    try:
        value = _scalar(conn, "SELECT docray_schema_version()")
    except psycopg.Error:
        return None
    return str(value) if value is not None else None


def _add_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _object_key_from_source_uri(source_uri: str, bucket: str) -> str | None:
    parsed = urlparse(source_uri)
    if parsed.scheme != "s3" or parsed.netloc != bucket:
        return None
    key = parsed.path.lstrip("/")
    return key or None


def _document_blob_hashes(conn: psycopg.Connection[Any], bucket: str) -> dict[str, str]:
    """Return object key -> expected source-byte hash for stored documents."""
    rows = _q(
        conn,
        "SELECT source_uri, content_sha256 FROM documents "
        "WHERE source_uri IS NOT NULL AND content_sha256 IS NOT NULL",
    )
    expected: dict[str, str] = {}
    for source_uri, content_sha256 in rows:
        key = _object_key_from_source_uri(str(source_uri), bucket)
        if key is not None:
            expected[key] = str(content_sha256)
    return expected


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------


def create_backup(
    conn: psycopg.Connection[Any], store: ObjectStore, *, note: str | None = None
) -> dict[str, Any]:
    """Create a backup archive, upload it, and index it in ``backups``.

    Inserts a ``creating`` row, dumps the DB + blobs to a ``.tar.gz``, uploads it
    to ``backups/<id>.tar.gz``, and flips the row to ``available`` (or ``failed``
    with the error on any exception, which is re-raised).

    Returns:
        A summary dict: ``id``, ``object_key``, ``schema_version``,
        ``archive_bytes``, ``table_rows``, ``blob_count``, ``blob_bytes``,
        ``doc_count``, ``tables`` (per-table row counts).
    """
    # Preflight: a backup that cannot read a table must fail loudly and early,
    # naming it — not die on a raw COPY partway through with a half-written
    # archive. A missing grant means an incomplete backup, which is data loss on
    # restore, so this is a hard error, never a silent skip.
    blocked = unreadable_tables(conn, _covered_tables(conn))
    if blocked:
        raise RuntimeError(
            "backup writer (docray_batch) lacks SELECT on: "
            + ", ".join(blocked)
            + " — grant it (see migration 119) before backing up; refusing to write "
            "an incomplete archive"
        )
    backup_id = new_ulid_str()
    conn.execute(
        "INSERT INTO backups (id, status, note) VALUES (%s, 'creating', %s)",
        (backup_id, note),
    )
    try:
        summary = _build_and_upload(conn, store, backup_id)
    except Exception as exc:
        conn.execute(
            "UPDATE backups SET status = 'failed', error = %s WHERE id = %s",
            (str(exc), backup_id),
        )
        raise
    conn.execute(
        "UPDATE backups SET status = 'available', object_key = %(key)s, "
        "schema_version = %(sv)s, archive_bytes = %(ab)s, table_rows = %(tr)s, "
        "blob_count = %(bc)s, blob_bytes = %(bb)s, doc_count = %(dc)s WHERE id = %(id)s",
        {
            "key": summary["object_key"],
            "sv": summary["schema_version"],
            "ab": summary["archive_bytes"],
            "tr": summary["table_rows"],
            "bc": summary["blob_count"],
            "bb": summary["blob_bytes"],
            "dc": summary["doc_count"],
            "id": backup_id,
        },
    )
    return summary


def _build_and_upload(
    conn: psycopg.Connection[Any], store: ObjectStore, backup_id: str
) -> dict[str, Any]:
    tables = _covered_tables(conn)
    cols_by_table = {t: _columns(conn, t) for t in tables}
    progress_total = len(tables) + 2
    report_progress(0, progress_total, "Preparing backup archive")
    buf = io.BytesIO()
    counts: dict[str, int] = {}
    table_rows = 0

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Consistent point-in-time snapshot of the whole database.
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            for index, table in enumerate(tables):
                report_progress(index, progress_total, f"Backing up table {table}")
                cols = cols_by_table[table]
                n = _scalar(conn, f'SELECT count(*) FROM "{table}"')
                counts[table] = int(n)
                table_rows += int(n)
                payload = io.BytesIO()
                with (
                    conn.cursor() as cur,
                    cur.copy(
                        f'COPY "{table}" ({_col_list(cols)}) TO STDOUT (FORMAT binary)'
                    ) as copy,
                ):
                    for chunk in copy:
                        payload.write(chunk)
                _add_bytes(tar, f"db/{table}.bin", payload.getvalue())
            sequences = _dump_sequences(conn)
        doc_count = int(_scalar(conn, "SELECT count(*) FROM documents"))

        # Blobs (everything except prior backup archives).
        report_progress(len(tables), progress_total, "Backing up object blobs")
        blob_keys = [k for k in store.list_objects() if not k.startswith(_BACKUP_PREFIX)]
        expected_hashes = _document_blob_hashes(conn, store.bucket)
        blob_manifest: list[dict[str, Any]] = []
        blob_bytes = 0
        for key in blob_keys:
            data = verify_bytes(
                store.get(key),
                expected_hashes.get(key),
                context=f"object-store blob {key}",
            )
            actual_sha = sha256_hex(data)
            blob_bytes += len(data)
            blob_manifest.append({"key": key, "sha256": actual_sha, "bytes": len(data)})
            _add_bytes(tar, f"blobs/{key}", data)

        manifest = {
            "archive_version": _ARCHIVE_VERSION,
            "backup_id": backup_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": _schema_version(conn),
            "tables": [{"name": t, "columns": cols_by_table[t], "rows": counts[t]} for t in tables],
            "sequences": sequences,
            "blob_count": len(blob_keys),
            "blob_bytes": blob_bytes,
            "blobs": blob_manifest,
            "doc_count": doc_count,
        }
        _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=1).encode("utf-8"))

    archive = buf.getvalue()
    object_key = f"{_BACKUP_PREFIX}{backup_id}.tar.gz"
    report_progress(len(tables) + 1, progress_total, "Uploading backup archive")
    store.put(object_key, archive, "application/gzip")
    return {
        "id": backup_id,
        "object_key": object_key,
        "schema_version": manifest["schema_version"],
        "archive_bytes": len(archive),
        "table_rows": table_rows,
        "blob_count": len(blob_keys),
        "blob_bytes": blob_bytes,
        "doc_count": doc_count,
        "tables": counts,
    }


def _dump_sequences(conn: psycopg.Connection[Any]) -> dict[str, list[Any]]:
    seqs = _q(
        conn,
        "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public'",
    )
    out: dict[str, list[Any]] = {}
    for (name,) in seqs:
        row = _q1(conn, f'SELECT last_value, is_called FROM "{name}"')
        if row is None:
            continue
        out[name] = [int(row[0]), bool(row[1])]
    return out


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------


def restore_from_archive(
    conn: psycopg.Connection[Any], store: ObjectStore, archive: bytes
) -> dict[str, Any]:
    """Restore the corpus (DB data + blobs) from an in-memory archive.

    The target DB must be at the archive's schema version. Rewrites every covered
    table and re-uploads every blob. Returns a summary of what was restored.

    Raises:
        ValueError: on a schema-version mismatch or a malformed archive.
    """
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        manifest = json.loads(_read_member(tar, "manifest.json"))
        current = _schema_version(conn)
        if manifest.get("schema_version") != current:
            raise ValueError(
                f"schema mismatch: archive {manifest.get('schema_version')!r} "
                f"!= database {current!r}; run migrations to the archive's version first"
            )
        table_specs = manifest["tables"]
        # One atomic transaction with FK/trigger enforcement deferred (superuser).
        with conn.transaction():
            conn.execute("SET LOCAL session_replication_role = replica")
            for spec in table_specs:
                conn.execute(f'DELETE FROM "{spec["name"]}"')
            for spec in table_specs:
                data = _read_member(tar, f"db/{spec['name']}.bin")
                if not data:
                    continue
                copy_sql = (
                    f'COPY "{spec["name"]}" ({_col_list(spec["columns"])}) '
                    "FROM STDIN (FORMAT binary)"
                )
                with conn.cursor() as cur, cur.copy(copy_sql) as copy:
                    copy.write(data)
            for name, (last_value, is_called) in manifest["sequences"].items():
                conn.execute(
                    "SELECT setval(%s, %s, %s)", (f'public."{name}"', last_value, is_called)
                )
        blobs = _restore_blobs(tar, store, manifest)

    return {
        "schema_version": manifest["schema_version"],
        "tables_restored": len(table_specs),
        "table_rows": sum(s["rows"] for s in table_specs),
        "blobs_restored": blobs,
        "doc_count": manifest.get("doc_count"),
    }


def restore_backup(
    conn: psycopg.Connection[Any], store: ObjectStore, backup_id: str
) -> dict[str, Any]:
    """Restore the backup identified by *backup_id* from the object store."""
    row = _q1(conn, "SELECT object_key, status FROM backups WHERE id = %s", (backup_id,))
    if row is None:
        raise ValueError(f"unknown backup {backup_id!r}")
    object_key = row[0]
    if not object_key:
        raise ValueError(f"backup {backup_id!r} has no archive (status {row[1]!r})")
    archive = store.get(object_key)
    return restore_from_archive(conn, store, archive)


def prune_backups(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    *,
    keep_latest: int,
    max_age_days: int | None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Delete backup archives outside the configured retention window.

    The newest ``keep_latest`` available archives are always retained. Older
    archives are pruned when they exceed ``max_age_days``; when
    ``max_age_days`` is ``None``, every archive beyond ``keep_latest`` is
    pruned.
    """
    if keep_latest < 0:
        raise ValueError("keep_latest must be non-negative")
    if max_age_days is not None and max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")
    cutoff = None
    if max_age_days is not None:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=max_age_days)

    rows = _q(
        conn,
        "SELECT id, object_key, created_at FROM backups "
        "WHERE status = 'available' AND object_key IS NOT NULL "
        "ORDER BY created_at DESC, id DESC",
    )
    pruned: list[dict[str, str]] = []
    for idx, (backup_id, object_key, created_at) in enumerate(rows):
        if idx < keep_latest:
            continue
        if cutoff is not None and created_at >= cutoff:
            continue
        store.delete(str(object_key))
        conn.execute("DELETE FROM backups WHERE id = %s", (backup_id,))
        pruned.append({"id": str(backup_id), "object_key": str(object_key)})
    return pruned


def _read_member(tar: tarfile.TarFile, name: str) -> bytes:
    try:
        fh = tar.extractfile(name)
    except KeyError:
        fh = None
    if fh is None:
        return b""
    return fh.read()


def _restore_blobs(tar: tarfile.TarFile, store: ObjectStore, manifest: dict[str, Any]) -> int:
    expected = {str(blob["key"]): str(blob["sha256"]) for blob in manifest.get("blobs") or []}
    n = 0
    for member in tar.getmembers():
        if member.isfile() and member.name.startswith("blobs/"):
            fh = tar.extractfile(member)
            if fh is None:
                continue
            key = member.name[len("blobs/") :]
            data = verify_bytes(fh.read(), expected.get(key), context=f"backup blob {key}")
            store.put(key, data, "application/octet-stream")
            n += 1
    return n
