"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    database_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            database_url=os.environ.get(
                "DATABASE_URL",
                "postgresql://docray:docray@localhost:5432/docray",
            ),
            s3_endpoint=os.environ.get("S3_ENDPOINT", "http://localhost:9000"),
            s3_access_key=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
            s3_secret_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
            s3_bucket=os.environ.get("S3_BUCKET", "documents"),
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
        )
