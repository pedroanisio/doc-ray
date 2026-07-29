#!/usr/bin/env python3
"""Reject ambiguous or incomplete documentation-quarantine ignore policy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MERGE_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
_REQUIRED_RULES = {
    ".doc-quarantine/",
    "doc-hygiene-report.md",
    "doc-quarantine.sh",
    "quarantine-manifest.json",
    "quarantine-summaries.md",
}


def policy_problems(root: Path) -> list[str]:
    """Return concrete problems in the repository quarantine ignore policy."""
    path = root / ".gitignore"
    if not path.is_file():
        return ["missing .gitignore"]
    lines = path.read_text(encoding="utf-8").splitlines()
    problems = [
        f".gitignore:{line_number}: merge-conflict marker {line.strip()!r}"
        for line_number, line in enumerate(lines, start=1)
        if any(marker in line for marker in _MERGE_MARKERS)
    ]
    rules = {line.strip() for line in lines if line.strip() and not line.startswith("#")}
    problems.extend(
        f".gitignore: missing required ignore rule {rule!r}"
        for rule in sorted(_REQUIRED_RULES - rules)
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Run the quarantine-policy gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_ROOT)
    args = parser.parse_args(argv)
    problems = policy_problems(args.repo_root)
    if problems:
        print("documentation quarantine policy drift:", file=sys.stderr)
        print("\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)
        return 1
    print("documentation quarantine policy OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
