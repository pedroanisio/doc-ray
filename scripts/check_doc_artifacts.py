#!/usr/bin/env python3
"""Verify checksum manifests that bind Markdown sources to binary documents."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_MANIFEST = _ROOT / "docs" / "diretrizes-de-design-2027.pdf.sha256"
_LINE_RE = re.compile(r"^([0-9a-f]{64})  ([^/\\]+)$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_checksum_file(manifest: Path) -> list[str]:
    """Return problems for a standard two-space SHA-256 manifest."""
    if not manifest.is_file():
        return [f"missing checksum manifest: {manifest}"]
    problems: list[str] = []
    entries = 0
    for line_number, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.startswith("#"):
            continue
        match = _LINE_RE.fullmatch(raw)
        if match is None:
            problems.append(f"{manifest}:{line_number}: invalid SHA-256 manifest line")
            continue
        entries += 1
        expected, filename = match.groups()
        target = manifest.parent / filename
        if not target.is_file():
            problems.append(f"{manifest}:{line_number}: missing {filename}")
            continue
        actual = _sha256(target)
        if actual != expected:
            problems.append(
                f"{manifest}:{line_number}: {filename} checksum is {actual}, expected {expected}"
            )
    if entries < 2:
        problems.append(f"{manifest}: expected checksums for both source and binary artifact")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Run every requested documentation-artifact checksum gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="*", type=Path)
    args = parser.parse_args(argv)
    manifests = args.manifests or [_DEFAULT_MANIFEST]
    problems = [problem for path in manifests for problem in check_checksum_file(path)]
    if problems:
        print("documentation artifact drift:", file=sys.stderr)
        print("\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)
        return 1
    print("documentation artifacts OK: source and binary checksums match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
