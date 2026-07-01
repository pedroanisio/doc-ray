"""CLI entrypoint: re-run spaCy annotation for stored documents.

The canonical ``python -m doc_ray.entrypoints.reannotate`` surface. Argument parsing
and logic live in :mod:`doc_ray.application.reannotate`; this is the thin driving adapter.
"""

from __future__ import annotations

from doc_ray.application.reannotate import main

if __name__ == "__main__":
    raise SystemExit(main())
