"""CLI entrypoint: annotate documents with rule-based lexemes.

The canonical ``python -m doc_ray.entrypoints.annotate`` surface. Argument parsing
and logic live in :mod:`doc_ray.adapters.annotation`; this is the thin driving adapter.
"""

from __future__ import annotations

from doc_ray.adapters.annotation import main

if __name__ == "__main__":
    raise SystemExit(main())
