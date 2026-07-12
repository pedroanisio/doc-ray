"""BL-114 regression guards: the committed uv.lock is enforced, not decorative.

Three invariants:

1. Every requirement in requirements.txt has a matching, version-satisfying
   entry in uv.lock (drift between the two fails here as well as in CI's
   authoritative ``uv lock --check`` step).
2. The Dockerfile installs the runtime set from the lockfile (``uv sync
   --locked``), never plain ``pip install -r requirements.txt``.
3. CI installs from the lockfile the same way and carries the
   ``uv lock --check`` drift gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

_ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENTS = _ROOT / "requirements.txt"
_UV_LOCK = _ROOT / "uv.lock"
_DOCKERFILE = _ROOT / "Dockerfile"
_CI_WORKFLOW = _ROOT / ".github" / "workflows" / "ci.yml"


def _parse_requirements(text: str) -> list[Requirement]:
    lines = [line.strip() for line in text.splitlines()]
    return [Requirement(line) for line in lines if line and not line.startswith("#")]


def _locked_versions(lock_text: str) -> dict[str, str]:
    lock = tomllib.loads(lock_text)
    return {
        canonicalize_name(pkg["name"]): pkg.get("version", "") for pkg in lock.get("package", [])
    }


def _requirements_missing_from_lock(requirements: str, lock: str) -> list[str]:
    """Return requirement names absent from the lock or locked outside their specifier."""
    locked = _locked_versions(lock)
    missing = []
    for req in _parse_requirements(requirements):
        name = canonicalize_name(req.name)
        if name not in locked:
            missing.append(f"{req.name} (not locked)")
        elif req.specifier and not req.specifier.contains(locked[name], prereleases=True):
            missing.append(f"{req.name} (locked {locked[name]} violates {req.specifier})")
    return missing


def test_every_requirement_is_satisfied_by_the_lockfile():
    drifted = _requirements_missing_from_lock(
        _REQUIREMENTS.read_text(encoding="utf-8"), _UV_LOCK.read_text(encoding="utf-8")
    )
    assert not drifted, (
        f"requirements.txt and uv.lock have drifted: {drifted}. "
        "Run `uv lock` and commit the result."
    )


def test_drift_checker_detects_an_unlocked_requirement():
    lock = _UV_LOCK.read_text(encoding="utf-8")
    drifted = _requirements_missing_from_lock("left-pad==1.*\n", lock)
    assert drifted == ["left-pad (not locked)"]


def test_drift_checker_detects_a_specifier_violation():
    requirements = _REQUIREMENTS.read_text(encoding="utf-8")
    fake_lock = '[[package]]\nname = "flask"\nversion = "0.1"\n'
    drifted = _requirements_missing_from_lock("Flask==3.1.*\n" + requirements, fake_lock)
    assert any("violates" in entry for entry in drifted)


def test_dockerfile_installs_from_the_lockfile():
    text = _DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"uv sync\b.*--locked", text), (
        "Dockerfile must install dependencies with `uv sync --locked` (BL-114)"
    )
    assert not re.search(r"pip install\b.*-r\s*requirements", text), (
        "Dockerfile must not bypass uv.lock with plain `pip install -r requirements*`"
    )


def test_ci_installs_from_the_lockfile_and_gates_drift():
    text = _CI_WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"pip install\s+-r\s+requirements\.txt", text), (
        "CI must not install the runtime set with plain pip against requirements.txt"
    )
    assert text.count("uv sync --locked") >= 2, (
        "both CI jobs must install the runtime set from uv.lock"
    )
    assert "uv lock --check" in text, "CI must carry the uv.lock drift gate"
