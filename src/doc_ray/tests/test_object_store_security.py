"""Security regressions for the S3-compatible object store adapter."""

from __future__ import annotations

import warnings

import pytest

from doc_ray.adapters import object_store
from doc_ray.config import Config


class _FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []

    def put_object(self, **kwargs: object) -> None:
        self.put_calls.append(kwargs)


def _base_s3_env(monkeypatch: pytest.MonkeyPatch, *, endpoint: str) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://docray:docray@postgres:5432/docray")
    monkeypatch.setenv("S3_ENDPOINT", endpoint)
    monkeypatch.setenv("S3_ACCESS_KEY", "access")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    monkeypatch.setenv("S3_BUCKET", "documents")
    monkeypatch.setenv("S3_REGION", "us-east-1")


def test_object_store_warns_on_plain_http_endpoint_without_opt_in(monkeypatch):
    _base_s3_env(monkeypatch, endpoint="http://objects.example.internal:9000")
    fake_client = _FakeS3Client()
    monkeypatch.setattr(object_store.boto3, "client", lambda *args, **kwargs: fake_client)

    with pytest.warns(RuntimeWarning, match="S3_ENDPOINT uses plaintext http://"):
        object_store.ObjectStore(Config.from_env())


def test_object_store_plain_http_opt_in_suppresses_warning(monkeypatch):
    _base_s3_env(monkeypatch, endpoint="http://minio:9000")
    monkeypatch.setenv("S3_ALLOW_INSECURE_ENDPOINT", "1")
    fake_client = _FakeS3Client()
    monkeypatch.setattr(object_store.boto3, "client", lambda *args, **kwargs: fake_client)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        object_store.ObjectStore(Config.from_env())

    assert [warning for warning in caught if warning.category is RuntimeWarning] == []


def test_object_store_put_requests_configured_server_side_encryption(monkeypatch):
    _base_s3_env(monkeypatch, endpoint="https://objects.example.internal")
    monkeypatch.setenv("S3_SERVER_SIDE_ENCRYPTION", "aws:kms")
    monkeypatch.setenv("S3_SSE_KMS_KEY_ID", "alias/doc-ray")
    fake_client = _FakeS3Client()
    monkeypatch.setattr(object_store.boto3, "client", lambda *args, **kwargs: fake_client)

    store = object_store.ObjectStore(Config.from_env())
    uri = store.put("ab/hash.pdf", b"document", "application/pdf")

    assert uri == "s3://documents/ab/hash.pdf"
    assert fake_client.put_calls == [
        {
            "Bucket": "documents",
            "Key": "ab/hash.pdf",
            "Body": b"document",
            "ContentType": "application/pdf",
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": "alias/doc-ray",
        }
    ]
