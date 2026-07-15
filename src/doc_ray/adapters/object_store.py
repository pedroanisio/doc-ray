"""Object storage for original document bytes (S3 / MinIO compatible)."""

from __future__ import annotations

import warnings
from urllib.parse import urlparse

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from doc_ray.config import Config


def _is_missing_bucket(exc: ClientError) -> bool:
    """Return whether *exc* is a 404 ``NoSuchBucket`` (vs. any other S3 error).

    ``head_bucket`` answers 404 for a bucket that does not exist and 403 for one
    that exists but is not readable by these credentials; only the former should
    trigger a ``create_bucket``.
    """
    error = exc.response.get("Error", {})
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 or error.get("Code") in {"404", "NoSuchBucket"}


def _warn_if_plaintext_endpoint(cfg: Config) -> None:
    parsed = urlparse(cfg.s3_endpoint)
    if parsed.scheme.lower() == "http" and not cfg.s3_allow_insecure_endpoint:
        warnings.warn(
            "S3_ENDPOINT uses plaintext http://; use https:// for non-local object stores "
            "or set S3_ALLOW_INSECURE_ENDPOINT=1 only for local development.",
            RuntimeWarning,
            stacklevel=3,
        )


class ObjectStore:
    """Thin S3/MinIO client for storing and locating original document bytes."""

    def __init__(self, cfg: Config) -> None:
        """Build the S3 client from the runtime configuration.

        Args:
            cfg: Runtime configuration carrying the S3 endpoint and credentials.
        """
        _warn_if_plaintext_endpoint(cfg)
        self._bucket = cfg.s3_bucket
        self.bucket = cfg.s3_bucket  # public: recorded in provenance
        self._server_side_encryption = cfg.s3_server_side_encryption
        self._sse_kms_key_id = cfg.s3_sse_kms_key_id
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint,
            aws_access_key_id=cfg.s3_access_key,
            aws_secret_access_key=cfg.s3_secret_key,
            region_name=cfg.s3_region,
            config=BotoConfig(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        """Create the configured bucket if it does not already exist (idempotent).

        Probes with ``head_bucket``, a bucket-scoped operation, rather than
        ``list_buckets``, which is account-wide and denied to the least-privilege,
        bucket-scoped keys production uses (see docs/production-setup.md). Only a
        genuine 404 triggers creation; any other error (403, endpoint down, bad
        credentials) propagates so it surfaces instead of being masked.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if not _is_missing_bucket(exc):
                raise
            self._client.create_bucket(Bucket=self._bucket)

    def ping(self) -> None:
        """Cheap read-only reachability probe for readiness checks.

        Heads the configured bucket (a no-side-effect, bucket-scoped call) purely to
        confirm the endpoint answers and the credentials are accepted. Uses
        ``head_bucket`` rather than the account-wide ``list_buckets`` so a
        least-privilege, bucket-scoped key does not make a healthy store read as
        down. Raises the underlying boto/botocore error when the store is
        unreachable or the credentials are rejected, so callers can turn that into a
        readiness signal.
        """
        self._client.head_bucket(Bucket=self._bucket)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Upload bytes and return the canonical ``s3://`` URI.

        Args:
            key: Object key to store the bytes under.
            data: Raw object bytes.
            content_type: MIME type recorded on the stored object.

        Returns:
            The canonical ``s3://bucket/key`` URI of the stored object.
        """
        put_kwargs: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": key,
            "Body": data,
            "ContentType": content_type,
        }
        if self._server_side_encryption:
            put_kwargs["ServerSideEncryption"] = self._server_side_encryption
            if self._sse_kms_key_id:
                put_kwargs["SSEKMSKeyId"] = self._sse_kms_key_id
        self._client.put_object(**put_kwargs)
        return f"s3://{self._bucket}/{key}"

    def get(self, key: str) -> bytes:
        """Fetch and return the stored object bytes for *key*.

        Args:
            key: Object key to read.

        Returns:
            The raw object bytes.
        """
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body: bytes = response["Body"].read()
        return body

    def list_objects(self, prefix: str = "") -> list[str]:
        """Return every object key under *prefix* (all keys when empty).

        Args:
            prefix: Key prefix to filter by; empty lists the whole bucket.

        Returns:
            A sorted list of object keys.
        """
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)

    def delete(self, key: str) -> None:
        """Delete one object key if it exists.

        Args:
            key: Object key to remove from the configured bucket.
        """
        self._client.delete_object(Bucket=self._bucket, Key=key)
