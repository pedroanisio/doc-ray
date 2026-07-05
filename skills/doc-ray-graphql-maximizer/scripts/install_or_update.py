#!/usr/bin/env python3
"""Install or update the Doc-Ray GraphQL maximizer skill."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_NAME = "doc-ray-graphql-maximizer"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_dest() -> Path:
    base = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"
    return base / SKILL_NAME


def read_version(root: Path) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not SEMVER_RE.match(version):
        raise ValueError(f"{root / 'VERSION'} is not SemVer MAJOR.MINOR.PATCH: {version!r}")
    return version


def version_tuple(version: str) -> tuple[int, int, int]:
    if not SEMVER_RE.match(version):
        raise ValueError(f"invalid SemVer: {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def copy_skill(src: Path, dest: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, ignore=ignore)


def run_drift_check(src: Path) -> None:
    checker = src / "scripts" / "check_doc_ray_drift.py"
    subprocess.run([sys.executable, str(checker)], check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=default_dest(), help="Install destination.")
    parser.add_argument("--force", action="store_true", help="Overwrite even if installed is newer.")
    parser.add_argument("--dry-run", action="store_true", help="Print the action without copying.")
    parser.add_argument("--skip-drift-check", action="store_true", help="Do not run drift check first.")
    args = parser.parse_args(argv)

    src = skill_root()
    src_version = read_version(src)
    installed_version: str | None = None
    if (args.dest / "VERSION").exists():
        installed_version = read_version(args.dest)
        if version_tuple(installed_version) > version_tuple(src_version) and not args.force:
            raise SystemExit(
                f"Refusing to replace newer installed {installed_version} with {src_version}. "
                "Use --force to override."
            )

    if not args.skip_drift_check:
        run_drift_check(src)

    action = "install" if installed_version is None else f"update {installed_version} -> {src_version}"
    print(f"{action}: {src} -> {args.dest}")
    if args.dry_run:
        return 0
    copy_skill(src, args.dest)
    print(f"installed {SKILL_NAME} {src_version} at {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

