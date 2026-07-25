#!/usr/bin/env python3
"""Thin launcher for the PostgreSQL capacity model (see scripts/capacity_model/).

Exists so the tool runs from the repo root without PYTHONPATH setup::

    python scripts/run_capacity_model.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capacity_model.__main__ import main  # noqa: E402 - path bootstrap first

if __name__ == "__main__":
    raise SystemExit(main())
