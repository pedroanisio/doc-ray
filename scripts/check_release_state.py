#!/usr/bin/env python3
"""Require the canonical application version to have the newest annotated Git tag."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SEMVER_TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def latest_semver_tag(tags: list[str]) -> str | None:
    """Return the numerically newest strict ``vX.Y.Z`` tag."""
    parsed = [
        (tuple(int(part) for part in match.groups()), tag)
        for tag in tags
        if (match := _SEMVER_TAG_RE.fullmatch(tag))
    ]
    return max(parsed)[1] if parsed else None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _canonical_version(root: Path) -> str:
    text = (root / "src" / "doc_ray" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if match is None:
        raise ValueError("cannot read doc_ray.__version__")
    return match.group(1)


def release_problems(root: Path) -> list[str]:
    """Return tag/version drift diagnostics for a Git checkout."""
    version = _canonical_version(root)
    expected = f"v{version}"
    tags_result = _git(root, "tag", "--list")
    if tags_result.returncode != 0:
        return [f"cannot list Git tags: {tags_result.stderr.strip()}"]
    tags = tags_result.stdout.splitlines()
    problems: list[str] = []
    latest = latest_semver_tag(tags)
    if expected not in tags:
        problems.append(f"canonical version {version} has no Git tag {expected}")
    if latest != expected:
        problems.append(f"newest SemVer tag is {latest or 'none'}, expected {expected}")
    if expected in tags:
        tag_type = _git(root, "cat-file", "-t", expected)
        if tag_type.returncode != 0 or tag_type.stdout.strip() != "tag":
            problems.append(f"{expected} must be an annotated tag")
        ancestry = _git(root, "merge-base", "--is-ancestor", f"{expected}^{{}}", "HEAD")
        if ancestry.returncode != 0:
            problems.append(f"{expected} does not point to an ancestor of HEAD")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Run the release/tag alignment gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_ROOT)
    args = parser.parse_args(argv)
    problems = release_problems(args.repo_root)
    if problems:
        print("release-state drift:", file=sys.stderr)
        print("\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)
        return 1
    print("release state OK: canonical version matches the newest annotated Git tag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
