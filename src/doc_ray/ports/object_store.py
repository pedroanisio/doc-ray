"""Port: content-addressed blob storage for original document bytes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ObjectStore(Protocol):
    """Store original bytes and return a stable URI.

    Fulfilled by ``doc_ray.adapters.object_store.ObjectStore`` (MinIO / S3).

    Attributes:
        bucket: Name of the bucket the store writes to.
    """

    bucket: str

    def ensure_bucket(self) -> None:
        """Create the configured bucket if it does not already exist."""
        ...

    def put(self, key: str, data: bytes, content_type: str) -> str:
        """Upload bytes; return the canonical ``s3://`` URI.

        Args:
            key: Object key (path) within the bucket.
            data: Raw bytes to store.
            content_type: MIME type recorded on the object.

        Returns:
            The canonical ``s3://`` URI of the stored object.
        """
        ...

    def get(self, key: str) -> bytes:
        """Fetch and return the stored object bytes for *key*.

        Args:
            key: Object key (path) within the bucket.

        Returns:
            The raw object bytes.
        """
        ...

    def list_objects(self, prefix: str = "") -> list[str]:
        """Return every object key under *prefix* (all keys when empty)."""
        ...

    def delete(self, key: str) -> None:
        """Delete one object key if it exists."""
        ...
