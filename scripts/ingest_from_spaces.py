#!/usr/bin/env python3
"""Idempotently ingest documents from a DigitalOcean Spaces (S3) bucket into doc-ray.

The source bucket (default ``doc-ray-dev`` at ``sfo3.digitaloceanspaces.com``) is
listed over the S3 API, each supported object is streamed through the same
:func:`doc_ray.application.pipeline.ingest_file` primitive the ``cli`` entrypoint
uses, and the original bytes land in doc-ray's own content-addressed object store
(``S3_ENDPOINT``/``S3_BUCKET``). The source bucket is read-only; nothing is written
back to it.

Idempotency has two layers, so re-running is always safe:

1. **Authoritative** — doc-ray dedupes on the SHA-256 of the content
   (``documents.content_sha256`` is ``UNIQUE``; :func:`ingest_file` returns
   ``duplicate`` for already-known bytes). Re-running never creates a second
   document, even if the local manifest is deleted.
2. **Efficient** — a local JSON manifest keyed by object key + ETag lets an
   unchanged object be skipped *without downloading it again*. An object whose
   ETag changed (i.e. it was replaced in Spaces) is re-ingested; its new content
   becomes a new document, the old one is left intact.

The manifest is an optimisation, not the source of truth. If the corpus is wiped
(e.g. ``make truncate-db``) while the manifest still says "ingested", re-run with
``--force`` (or delete the manifest) to repopulate — the SHA-256 dedup then keeps
the result correct.

Usage::

    export SPACES_KEY=... SPACES_SECRET=...          # omit for a public bucket
    set -a; source .env; set +a                       # DATABASE_URL + S3_* sink config
    python scripts/ingest_from_spaces.py --prefix incoming/
    python scripts/ingest_from_spaces.py --dry-run    # show planned actions only

Exit codes: 0 = no failures; 2 = listing failed or at least one object failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.client import BaseClient
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from doc_ray.adapters import persistence as db
from doc_ray.adapters.extraction import is_supported_extension
from doc_ray.adapters.object_store import ObjectStore
from doc_ray.application.pipeline import IngestResult, ingest_file
from doc_ray.config import Config

DEFAULT_ENDPOINT = "https://sfo3.digitaloceanspaces.com"
DEFAULT_BUCKET = "doc-ray-dev"
DEFAULT_REGION = "sfo3"
DEFAULT_STATE = ".ingest-state/doc-ray-dev.json"

#: Manifest statuses that mean "already handled" — an unchanged object recorded
#: with one of these is skipped without being downloaded again.
_TERMINAL_OK = frozenset({"ingested", "duplicate"})

#: Short display tags per ingest outcome.
_TAGS = {"ingested": "OK ", "duplicate": "DUP", "failed": "ERR"}


@dataclass(frozen=True)
class SourceObject:
    """A listed object in the source bucket.

    Attributes:
        key: The object key.
        etag: The S3 ETag with surrounding quotes stripped. Compared across runs
            purely for change detection; never interpreted as an MD5 (Spaces
            multipart ETags are not plain MD5 digests).
        size: Object size in bytes.
    """

    key: str
    etag: str
    size: int


def decide_action(obj: SourceObject, prior: dict[str, Any] | None, *, force: bool) -> str:
    """Return ``"ingest"`` or ``"skip"`` for *obj* given its recorded manifest state.

    A never-seen object, a changed ETag, or a non-terminal prior outcome all
    trigger a (re-)ingest; an unchanged object that already ingested or deduped is
    skipped. ``force`` ignores the manifest entirely.

    Args:
        obj: The listed source object.
        prior: The manifest entry from a previous run, or ``None``.
        force: When true, always ingest.

    Returns:
        ``"ingest"`` or ``"skip"``.
    """
    if force or prior is None:
        return "ingest"
    if prior.get("etag") != obj.etag:
        return "ingest"
    if prior.get("status") in _TERMINAL_OK:
        return "skip"
    return "ingest"


def supported_only(objects: list[SourceObject]) -> list[SourceObject]:
    """Return only the objects whose extension has a registered extractor.

    Directory-marker keys (ending in ``/``, empty suffix) and unsupported types
    are dropped so they never reach the ingest path.

    Args:
        objects: Listed source objects.

    Returns:
        The subset with a supported file extension, in the input order.
    """
    return [o for o in objects if is_supported_extension(Path(o.key).suffix)]


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    """Load the ingest manifest from *path* (empty when absent or unreadable).

    Args:
        path: Manifest file path.

    Returns:
        A ``{object_key: entry}`` mapping; empty if the file is missing, corrupt,
        or not an object.
    """
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    """Write the ingest manifest to *path*, creating parent directories.

    Args:
        path: Manifest file path.
        state: The manifest to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _state_entry(obj: SourceObject, status: str, document_id: str | None) -> dict[str, Any]:
    """Build a manifest entry recording this run's outcome for *obj*."""
    return {
        "etag": obj.etag,
        "size": obj.size,
        "status": status,
        "document_id": document_id,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _source_credentials(args: argparse.Namespace) -> tuple[str | None, str | None]:
    """Resolve source-bucket credentials from flags then environment.

    Order of precedence: explicit ``--access-key``/``--secret-key`` flags, then
    ``SPACES_KEY``/``SPACES_SECRET``, then ``AWS_ACCESS_KEY_ID``/
    ``AWS_SECRET_ACCESS_KEY``. ``(None, None)`` signals anonymous access, which
    works for a public bucket.
    """
    key = args.access_key or os.environ.get("SPACES_KEY") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret = (
        args.secret_key
        or os.environ.get("SPACES_SECRET")
        or os.environ.get("AWS_SECRET_ACCESS_KEY")
    )
    return key, secret


def build_source_client(
    endpoint: str, region: str, key: str | None, secret: str | None
) -> BaseClient:
    """Build a boto3 S3 client for the source bucket (anonymous when unkeyed).

    Args:
        endpoint: S3 endpoint URL, e.g. ``https://sfo3.digitaloceanspaces.com``.
        region: Region name, e.g. ``sfo3``.
        key: Access key id, or ``None`` for anonymous access.
        secret: Secret access key, or ``None`` for anonymous access.

    Returns:
        A configured boto3 S3 client pointed at the source bucket.
    """
    if key and secret:
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            config=BotoConfig(signature_version="s3v4"),
        )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=BotoConfig(signature_version=UNSIGNED),
    )


def list_source_objects(client: BaseClient, bucket: str, prefix: str) -> list[SourceObject]:
    """List every object under *prefix* in the source bucket, sorted by key.

    Args:
        client: Source S3 client.
        bucket: Source bucket name.
        prefix: Key prefix filter (``""`` lists the whole bucket).

    Returns:
        The listed objects sorted by key.
    """
    out: list[SourceObject] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            out.append(
                SourceObject(
                    key=obj["Key"],
                    etag=str(obj.get("ETag", "")).strip('"'),
                    size=int(obj.get("Size", 0)),
                )
            )
    return sorted(out, key=lambda o: o.key)


def _download(client: BaseClient, bucket: str, key: str) -> bytes:
    """Fetch and return the raw bytes of *key* from the source bucket."""
    response = client.get_object(Bucket=bucket, Key=key)
    body: bytes = response["Body"].read()
    return body


def ingest_object(conn: Any, store: ObjectStore, key: str, data: bytes) -> IngestResult:
    """Ingest downloaded *data* for *key* via the shared ingest primitive.

    The bytes are written to a temporary file carrying *key*'s basename so the
    extractor registry dispatches on the right suffix and the persisted record
    keeps a meaningful ``original_filename``. The blob is then re-stored,
    content-addressed, by :func:`ingest_file`.

    Args:
        conn: Live database connection.
        store: doc-ray's own object store (the ingest sink).
        key: Source object key; its basename names the document.
        data: Raw object bytes.

    Returns:
        The :class:`IngestResult` from :func:`ingest_file`.
    """
    with tempfile.TemporaryDirectory(prefix="docray-spaces-") as tmp_dir:
        tmp_path = Path(tmp_dir) / Path(key).name
        tmp_path.write_bytes(data)
        return ingest_file(conn, store, tmp_path)


def _ingest_one(
    conn: Any,
    store: ObjectStore,
    client: BaseClient,
    bucket: str,
    obj: SourceObject,
    state: dict[str, dict[str, Any]],
) -> str:
    """Download and ingest one object, updating *state* in place; return its status."""
    try:
        data = _download(client, bucket, obj.key)
    except (ClientError, BotoCoreError) as exc:
        print(f"  [ERR] {obj.key} -> download failed ({exc})")
        state[obj.key] = _state_entry(obj, "failed", None)
        return "failed"
    result = ingest_object(conn, store, obj.key, data)
    state[obj.key] = _state_entry(obj, result.status, result.document_id)
    tag = _TAGS.get(result.status, "?")
    detail = f"  ({result.detail})" if result.detail else ""
    print(f"  [{tag}] {obj.key} -> {result.document_id or '-'}{detail}")
    return result.status


def _report_dry_run(
    objects: list[SourceObject], state: dict[str, dict[str, Any]], *, force: bool
) -> int:
    """Print the planned action per object without touching the database or sink."""
    counts: dict[str, int] = {}
    for obj in objects:
        action = decide_action(obj, state.get(obj.key), force=force)
        counts[action] = counts.get(action, 0) + 1
        print(f"  [{action.upper():6}] {obj.key}")
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"Dry run: {summary or 'nothing to do'}")
    return 0


def run(args: argparse.Namespace) -> int:
    """Execute one full idempotent ingest pass; return the process exit code."""
    key, secret = _source_credentials(args)
    client = build_source_client(args.endpoint, args.region, key, secret)
    try:
        listed = list_source_objects(client, args.bucket, args.prefix)
    except (ClientError, BotoCoreError) as exc:
        print(f"error: cannot list s3://{args.bucket}/{args.prefix}: {exc}", file=sys.stderr)
        if not (key and secret):
            print("hint: set SPACES_KEY/SPACES_SECRET for a private bucket.", file=sys.stderr)
        return 2

    supported = supported_only(listed)
    objects = supported[: args.limit] if args.limit is not None else supported
    scope = f"s3://{args.bucket}/{args.prefix}" if args.prefix else f"s3://{args.bucket}"
    print(
        f"Source {scope}: {len(listed)} object(s), "
        f"{len(supported)} supported, {len(objects)} selected."
    )

    state_path = Path(args.state)
    state = load_state(state_path)

    if args.dry_run:
        return _report_dry_run(objects, state, force=args.force)

    counts = _ingest_all(args, client, objects, state)
    save_state(state_path, state)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"Done: {summary or 'nothing to do'}")
    return 2 if counts.get("failed", 0) else 0


def _ingest_all(
    args: argparse.Namespace,
    client: BaseClient,
    objects: list[SourceObject],
    state: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Ingest every actionable object under one connection; return outcome counts."""
    cfg = Config.from_env()
    store = ObjectStore(cfg)
    store.ensure_bucket()
    counts: dict[str, int] = {}
    with db.connect(cfg) as conn:
        for obj in objects:
            if decide_action(obj, state.get(obj.key), force=args.force) == "skip":
                counts["skipped"] = counts.get("skipped", 0) + 1
                continue
            status = _ingest_one(conn, store, client, args.bucket, obj, state)
            counts[status] = counts.get(status, 0) + 1
    return counts


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="ingest_from_spaces",
        description="Idempotently ingest a DigitalOcean Spaces bucket into doc-ray.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("DOCRAY_SOURCE_ENDPOINT", DEFAULT_ENDPOINT),
        help="S3 endpoint URL of the source bucket.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("DOCRAY_SOURCE_BUCKET", DEFAULT_BUCKET),
        help="Source bucket name.",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("DOCRAY_SOURCE_REGION", DEFAULT_REGION),
        help="Source region name.",
    )
    parser.add_argument("--prefix", default="", help="Only ingest keys under this prefix.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Ingest at most N supported objects."
    )
    parser.add_argument(
        "--state",
        default=os.environ.get("DOCRAY_SOURCE_STATE", DEFAULT_STATE),
        help="Path to the local idempotency manifest.",
    )
    parser.add_argument(
        "--access-key",
        default=None,
        help="Source access key (else SPACES_KEY / AWS_ACCESS_KEY_ID).",
    )
    parser.add_argument(
        "--secret-key",
        default=None,
        help="Source secret key (else SPACES_SECRET / AWS_SECRET_ACCESS_KEY).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-ingest even if the manifest says unchanged."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="List planned actions without ingesting."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the ingest pass."""
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
