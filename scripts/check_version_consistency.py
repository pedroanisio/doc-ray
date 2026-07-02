#!/usr/bin/env python3
"""Fail unless every declared SemVer surface agrees with ``doc_ray.__version__``.

doc-ray carries one global semantic version. Historically it drifted: migrations
021-023 added tables without bumping the schema version, and the packaging version
sat at 0.1.0 for the life of the project. This gate makes that class of drift a
hard error instead of a silent inconsistency.

Canonical source: ``src/doc_ray/__init__.py`` ``__version__``. Checked surfaces:
  * ``engineering_decisions.yml`` ``source_schema_version``
  * ``CHANGELOG.md`` top release heading ``## [X.Y.Z]``
  * the highest-numbered ``db/migrations/NNN_*.sql`` that writes ``schema_version``

Packaging (``pyproject.toml``) derives the version from ``__version__`` and so cannot
drift; ``docs/DATABASE.md`` is generated from the live schema and is out of scope here.
The check is stdlib-only and offline (no database), so it runs inside pre-commit.

Exit code 0 when everything agrees, 1 on any mismatch or missing surface.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SEMVER = r"\d+\.\d+\.\d+"


def _canonical_version() -> str:
    """Return ``doc_ray.__version__`` parsed from source (no import needed)."""
    text = (_ROOT / "src" / "doc_ray" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(rf'^__version__\s*=\s*"({_SEMVER})"', text, re.MULTILINE)
    if match is None:
        raise SystemExit("fatal: doc_ray.__version__ not found in src/doc_ray/__init__.py")
    return match.group(1)


def _decisions_version() -> str | None:
    """Return ``source_schema_version`` from engineering_decisions.yml, if present."""
    text = (_ROOT / "engineering_decisions.yml").read_text(encoding="utf-8")
    match = re.search(rf'^source_schema_version:\s*"({_SEMVER})"', text, re.MULTILINE)
    return match.group(1) if match else None


def _changelog_version() -> str | None:
    """Return the newest release version from CHANGELOG.md (first ``## [X.Y.Z]``)."""
    path = _ROOT / "CHANGELOG.md"
    if not path.exists():
        return None
    match = re.search(rf"^##\s*\[({_SEMVER})\]", path.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def _schema_migration_version() -> str | None:
    """Return the schema version set by the latest migration that writes it."""
    migrations = sorted((_ROOT / "db" / "migrations").glob("*.sql"))
    for path in reversed(migrations):
        text = path.read_text(encoding="utf-8")
        # Match both the seeding INSERT (019) and the bumping UPDATE (024+).
        hits = re.findall(rf"schema_version\b.*?'({_SEMVER})'", text, re.DOTALL)
        if hits:
            return hits[-1]
    return None


def main() -> int:
    """Compare every version surface to the canonical version; report mismatches."""
    canonical = _canonical_version()
    surfaces = {
        "engineering_decisions.yml (source_schema_version)": _decisions_version(),
        "CHANGELOG.md (latest release)": _changelog_version(),
        "db/migrations (latest schema_version)": _schema_migration_version(),
    }

    problems: list[str] = []
    for name, found in surfaces.items():
        if found is None:
            problems.append(f"  - {name}: no version found")
        elif found != canonical:
            problems.append(f"  - {name}: {found} != {canonical}")

    if problems:
        print(f"version drift against doc_ray.__version__ = {canonical}:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        return 1

    print(f"version consistency OK: all surfaces at {canonical}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
