"""ObjectStore must work with a bucket-scoped S3 key (no account-wide list).

Regression cover for the class of failure where a least-privilege, bucket-scoped
DigitalOcean Spaces key (the exact key production-setup.md prescribes) is denied
``ListBuckets`` — an account-wide operation — even though it can fully read and
write its own bucket. ``ensure_bucket`` and ``ping`` therefore must probe with
``head_bucket`` (bucket-scoped) rather than ``list_buckets``, or the ingest
crashes at startup and ``/readyz`` reports the store as down when it is healthy.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from doc_ray.adapters import object_store
from doc_ray.config import Config


def _client_error(status: int, code: str, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


class _FakeS3Client:
    """Records calls and replays scripted head_bucket behaviour.

    ``head_error`` is raised by ``head_bucket`` when set; otherwise the bucket is
    treated as present. ``list_buckets`` always raises to prove it is never used.
    """

    def __init__(self, head_error: ClientError | None = None) -> None:
        self.head_error = head_error
        self.calls: list[str] = []

    def head_bucket(self, **_kwargs: object) -> dict[str, object]:
        self.calls.append("head_bucket")
        if self.head_error is not None:
            raise self.head_error
        return {}

    def create_bucket(self, **_kwargs: object) -> dict[str, object]:
        self.calls.append("create_bucket")
        return {}

    def list_buckets(self, **_kwargs: object) -> dict[str, object]:  # pragma: no cover - guard
        self.calls.append("list_buckets")
        raise _client_error(403, "AccessDenied", "ListBuckets")


def _store(monkeypatch: pytest.MonkeyPatch, client: _FakeS3Client) -> object_store.ObjectStore:
    monkeypatch.setenv("DATABASE_URL", "postgresql://docray:docray@postgres:5432/docray")
    monkeypatch.setenv("S3_ENDPOINT", "https://nyc3.digitaloceanspaces.com")
    monkeypatch.setenv("S3_ACCESS_KEY", "access")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_BUCKET", "doc-ray-prd")
    monkeypatch.setenv("S3_REGION", "nyc3")
    monkeypatch.setattr(object_store.boto3, "client", lambda *a, **k: client)
    return object_store.ObjectStore(Config.from_env())


def test_ensure_bucket_no_ops_when_bucket_exists(monkeypatch):
    client = _FakeS3Client(head_error=None)
    _store(monkeypatch, client).ensure_bucket()
    assert client.calls == ["head_bucket"]  # existing bucket → no create, no list


def test_ensure_bucket_creates_only_on_404(monkeypatch):
    client = _FakeS3Client(head_error=_client_error(404, "404", "HeadBucket"))
    _store(monkeypatch, client).ensure_bucket()
    assert client.calls == ["head_bucket", "create_bucket"]


def test_ensure_bucket_never_calls_list_buckets(monkeypatch):
    client = _FakeS3Client(head_error=None)
    _store(monkeypatch, client).ensure_bucket()
    assert "list_buckets" not in client.calls


def test_ensure_bucket_propagates_non_404_errors(monkeypatch):
    denied = _client_error(403, "AccessDenied", "HeadBucket")
    client = _FakeS3Client(head_error=denied)
    with pytest.raises(ClientError):
        _store(monkeypatch, client).ensure_bucket()
    assert "create_bucket" not in client.calls  # do not attempt to create on 403


def test_ping_uses_head_bucket_not_list_buckets(monkeypatch):
    client = _FakeS3Client(head_error=None)
    _store(monkeypatch, client).ping()
    assert client.calls == ["head_bucket"]


def test_ping_propagates_unreachable_endpoint(monkeypatch):
    down = _client_error(500, "InternalError", "HeadBucket")
    client = _FakeS3Client(head_error=down)
    with pytest.raises(ClientError):
        _store(monkeypatch, client).ping()
