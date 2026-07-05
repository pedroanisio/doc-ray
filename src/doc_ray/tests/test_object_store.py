"""ObjectStore adapter against the real MinIO service (skips if unreachable)."""

from __future__ import annotations

import os
import uuid

import pytest

from doc_ray.adapters.object_store import ObjectStore
from doc_ray.config import Config


@pytest.fixture(scope="module")
def store() -> ObjectStore:
    cfg = Config.from_env()
    endpoint = os.environ.get("S3_ENDPOINT", cfg.s3_endpoint)
    s = ObjectStore(
        Config(
            database_url=cfg.database_url,
            s3_endpoint=endpoint,
            s3_access_key=cfg.s3_access_key,
            s3_secret_key=cfg.s3_secret_key,
            s3_bucket=cfg.s3_bucket,
            s3_region=cfg.s3_region,
        )
    )
    try:
        s.ensure_bucket()
    except Exception as exc:  # MinIO down → skip, not fail
        pytest.skip(f"object store unreachable: {exc}")
    return s


def test_ensure_bucket_is_idempotent(store):
    store.ensure_bucket()  # second call must be a no-op, not an error


def test_put_returns_canonical_uri_and_get_roundtrips(store):
    key = f"it/{uuid.uuid4().hex}.txt"
    body = b"object store round trip"
    uri = store.put(key, body, "text/plain")
    assert uri == f"s3://{store.bucket}/{key}"
    assert store.get(key) == body


def test_delete_removes_an_object_key(store):
    key = f"it/{uuid.uuid4().hex}.txt"
    store.put(key, b"delete me", "text/plain")
    assert key in store.list_objects("it/")

    store.delete(key)

    assert key not in store.list_objects("it/")
