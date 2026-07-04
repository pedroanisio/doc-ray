"""Pluggable database engine backends for db_analyzer.

Each engine implements :class:`~doc_ray.application.db_analyzer.engines.base.Engine`
and registers itself in :mod:`~doc_ray.application.db_analyzer.engines.registry`.
Only PostgreSQL ships today; adding another engine means implementing the ABC
against its own system catalog and registering it — nothing outside
``engines/`` needs to change.
"""

from __future__ import annotations
