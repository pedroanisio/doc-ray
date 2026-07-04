"""CLI entrypoint: analyze a database's structure and profile its data.

The canonical ``python -m doc_ray.entrypoints.db_analyze`` surface. Argument
parsing and orchestration live in :mod:`doc_ray.application.db_analyzer.run`;
this is the thin driving adapter.
"""

from __future__ import annotations

from doc_ray.application.db_analyzer.run import main

if __name__ == "__main__":
    raise SystemExit(main())
