"""Dependency-free ``.env`` fallback for operator scripts.

Operator scripts read config (``DATABASE_URL``, ``S3_BUCKET``, ...) from the real
environment, matching ``doc_ray.config.Config.from_env``. On the box those values
live in ``.env``/``.env.prod`` but are not always exported into the shell. This
helper fills only the *unset* keys from those files so a script run directly
("python scripts/foo.py") just works, while an explicit ``KEY=... python ...``
override always wins. No python-dotenv dependency is introduced.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repo root: this module lives in scripts/, so the parent's parent is the root.
_REPO = Path(__file__).resolve().parent.parent
# Files consulted, in order, ONLY for keys not already in the real environment.
_ENV_FALLBACK_FILES = (".env", ".env.prod")


def load_env_fallback(keys: tuple[str, ...]) -> str | None:
    """Fill missing ``keys`` from the repo's ``.env``/``.env.prod``.

    Real environment variables always win — a file value is only applied when
    the key is currently unset. Malformed lines, comments, and ``export``
    prefixes are tolerated.

    Args:
        keys: Environment variable names to populate if absent.

    Returns:
        The name of the file that supplied at least one value, or ``None`` when
        nothing needed loading (or no file provided a value).
    """
    if all(os.environ.get(k) for k in keys):
        return None
    for filename in _ENV_FALLBACK_FILES:
        path = _REPO / filename
        if not path.is_file():
            continue
        used = False
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            line = line.removeprefix("export ").lstrip()
            key, _, value = line.partition("=")
            key = key.strip()
            if key not in keys or os.environ.get(key):
                continue
            os.environ[key] = value.strip().strip('"').strip("'")
            used = True
        if used:
            return filename
    return None
