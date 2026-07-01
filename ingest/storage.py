"""Object storage for original document bytes (S3 / MinIO compatible)."""

from __future__ import annotations

import boto3
from botocore.client import Config as BotoConfig

from .config import Config


class ObjectStore:
    def __init__(self, cfg: Config):
        self._bucket = cfg.s3_bucket
        self.bucket = cfg.s3_bucket        # public: recorded in provenance
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

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Upload bytes and return the canonical s3:// URI."""
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"s3://{self._bucket}/{key}"
