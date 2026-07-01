"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable runtime settings for the database and object store.

    Attributes:
        database_url: PostgreSQL connection URL.
        s3_endpoint: Base URL of the S3-compatible object store.
        s3_access_key: Access key for the object store.
        s3_secret_key: Secret key for the object store.
        s3_bucket: Name of the bucket holding original document bytes.
        s3_region: Region the object store reports itself in.
    """

    database_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str

    @classmethod
    def from_env(cls) -> Config:
        """Build a ``Config`` from environment variables, applying defaults.

        Returns:
            A ``Config`` populated from ``DATABASE_URL`` and the ``S3_*``
            environment variables, falling back to local development defaults.
        """
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
