"""Object storage for original document bytes (S3 / MinIO compatible)."""

from __future__ import annotations

import boto3
from botocore.client import Config as BotoConfig

from doc_ray.config import Config


class ObjectStore:
    """Thin S3/MinIO client for storing and locating original document bytes."""

    def __init__(self, cfg: Config) -> None:
        """Build the S3 client from the runtime configuration.

        Args:
            cfg: Runtime configuration carrying the S3 endpoint and credentials.
        """
        self._bucket = cfg.s3_bucket
        self.bucket = cfg.s3_bucket  # public: recorded in provenance
        self._client = boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint,
            aws_access_key_id=cfg.s3_access_key,
            aws_secret_access_key=cfg.s3_secret_key,
            region_name=cfg.s3_region,
            config=BotoConfig(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not already exist (idempotent)."""
        existing = {b["Name"] for b in self._client.list_buckets().get("Buckets", [])}
        if self._bucket not in existing:
            self._client.create_bucket(Bucket=self._bucket)

    def ping(self) -> None:
        """Cheap read-only reachability probe for readiness checks.

        Lists buckets (a no-side-effect call) purely to confirm the endpoint answers.
        Raises the underlying boto/botocore error when the store is unreachable or the
        credentials are rejected, so callers can turn that into a readiness signal.
        """
        self._client.list_buckets()

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Upload bytes and return the canonical ``s3://`` URI.

        Args:
            key: Object key to store the bytes under.
            data: Raw object bytes.
            content_type: MIME type recorded on the stored object.

        Returns:
            The canonical ``s3://bucket/key`` URI of the stored object.
        """
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
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
