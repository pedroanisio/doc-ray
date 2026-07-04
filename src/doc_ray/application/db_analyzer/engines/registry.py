"""Lookup table mapping engine names to :class:`~.base.Engine` implementations.

Adding a new engine (MySQL, SQLite, ...) means writing a module beside
:mod:`postgres` and adding one line here — nothing else in db_analyzer changes.
"""

from __future__ import annotations

from doc_ray.application.db_analyzer.engines.base import Engine
from doc_ray.application.db_analyzer.engines.postgres import PostgresEngine

_ENGINES: dict[str, type[Engine]] = {
    "postgres": PostgresEngine,
    "postgresql": PostgresEngine,
}


def get_engine(name: str) -> Engine:
    """Instantiate the engine registered under ``name``.

    Args:
        name: Engine name, case-insensitive (e.g. ``"postgres"``).

    Returns:
        A fresh engine instance.

    Raises:
        ValueError: If no engine is registered under ``name``.
    """
    cls = _ENGINES.get(name.strip().lower())
    if cls is None:
        available = ", ".join(sorted(set(_ENGINES)))
        raise ValueError(f"unknown database engine {name!r} — available: {available}")
    return cls()
