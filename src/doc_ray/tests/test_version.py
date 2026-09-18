"""The single global SemVer and the consistency gate that keeps its surfaces aligned.

Guards the versioned pipeline (see scripts/check_version_consistency.py): the package
version, the engineering-decisions record, the changelog, and the latest schema
migration must all report the same semantic version, and the GraphQL API must expose it.
"""

from __future__ import annotations

import re
from pathlib import Path

import doc_ray

_ROOT = Path(__file__).resolve().parents[3]
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_semver():
    assert _SEMVER_RE.match(doc_ray.__version__), doc_ray.__version__


def test_webapp_epic_release_is_1_43_0_with_a_marker_migration():
    """Epic #23 shipped as one explicit, deployable project/schema release (1.43.0).

    Its marker migration is a permanent fact; the version advances past the epic as
    later work lands (genre classification → 1.45.0, the projection engine → 1.46.0, …) but must never regress
    below its release. Pinning ``__version__`` to the exact epic string was brittle —
    it broke on the very next feature bump — so this asserts the durable invariants
    instead: the marker migration exists and the current version is at least 1.43.0.
    """
    marker = _ROOT / "db" / "migrations" / "125_schema_version_1_43_0.sql"
    assert marker.is_file()
    assert "'1.43.0'" in marker.read_text(encoding="utf-8")
    assert tuple(int(p) for p in doc_ray.__version__.split(".")) >= (1, 43, 0)


def test_all_version_surfaces_agree():
    """The offline consistency gate must pass against the current tree."""
    import check_version_consistency as gate

    canonical = gate._canonical_version()
    assert canonical == doc_ray.__version__
    assert gate._decisions_version() == canonical
    assert gate._changelog_version() == canonical
    assert gate._schema_migration_version() == canonical
    assert gate.main() == 0  # end-to-end: no drift reported


def test_pyproject_does_not_hardcode_version():
    """Packaging derives the version from the attribute, so it cannot drift."""
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'attr = "doc_ray.__version__"' in pyproject
    assert not re.search(r'^version\s*=\s*"', pyproject, re.MULTILINE)


def test_graphql_exposes_version():
    from doc_ray.entrypoints.api.schema import build_schema

    result = build_schema().execute_sync("{ version }")
    assert result.errors is None, result.errors
    assert result.data["version"] == doc_ray.__version__
