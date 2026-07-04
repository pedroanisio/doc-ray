"""Document ingestion, decomposition, and annotation pipeline package."""

from __future__ import annotations

#: The project's single source of truth for its semantic version (SemVer
#: ``major.minor.patch``). Every other version surface — the packaging metadata
#: (``pyproject.toml`` reads this attribute), the database schema version
#: (``db/migrations`` / ``docray_schema_version()``), the changelog, and the API
#: ``version`` field — must agree with it. ``scripts/check_version_consistency.py``
#: enforces that agreement; bump this and the schema together, never in isolation.
__version__ = "1.4.0"

__all__ = ["__version__"]
