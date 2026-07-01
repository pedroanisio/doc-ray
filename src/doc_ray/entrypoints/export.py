"""CLI entrypoint: export stored documents as JSON.

The canonical ``python -m doc_ray.entrypoints.export`` surface (alongside ``cli``,
``migrate``, and ``worker``). Argument parsing and logic live in
:mod:`doc_ray.adapters.export`; this module is the thin driving adapter.
"""

from __future__ import annotations

from doc_ray.adapters.export import main

if __name__ == "__main__":
    raise SystemExit(main())
