"""CLI entrypoint: annotate documents with spaCy (tokens/deps/NER/…).

The canonical ``python -m doc_ray.entrypoints.annotate_spacy`` surface. Argument
parsing and logic live in :mod:`doc_ray.adapters.annotation_spacy`; this is the thin
driving adapter.
"""

from __future__ import annotations

from doc_ray.adapters.annotation_spacy import main

if __name__ == "__main__":
    raise SystemExit(main())
