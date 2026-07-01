"""CLI entrypoint: re-extract one stored document from its original bytes.

The canonical ``python -m doc_ray.entrypoints.reextract`` surface. Argument parsing
and logic live in :mod:`doc_ray.application.reextract`; this is the thin driving adapter.
"""

from __future__ import annotations

from doc_ray.application.reextract import main

if __name__ == "__main__":
    raise SystemExit(main())
