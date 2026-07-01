"""Port: content-addressed blob storage for original document bytes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ObjectStore(Protocol):
    """Store original bytes and return a stable URI.

    Fulfilled by ``doc_ray.adapters.object_store.ObjectStore`` (MinIO / S3).
    """
    bucket: str

    def ensure_bucket(self) -> None: ...

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Upload bytes; return the canonical ``s3://`` URI."""
        ...
