"""CLI entrypoint: report the health of the doc-ray Postgres instance.

The canonical ``python -m doc_ray.entrypoints.db_health`` surface. Argument
parsing and logic live in :mod:`doc_ray.application.db_health`; this is the
thin driving adapter.
"""

from __future__ import annotations

from doc_ray.application.db_health import main

if __name__ == "__main__":
    raise SystemExit(main())
