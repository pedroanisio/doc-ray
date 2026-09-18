#!/usr/bin/env python3
"""Release-audit gate C-1: the index must agree with the ignore policy.

An ignore rule protects only files that are not yet tracked, so a tracked file
matching one is a silent contradiction: the policy says "never ship" while the
index ships it. The same goes for a tracked symlink whose target is untracked or
ignored (it dangles in every clean clone) and for anything tracked under a
directory that exists to hold local output.

Findings:

* ``TRACKED-IGNORED`` — ``git ls-files -i -c --exclude-standard`` is non-empty.
* ``DANGLING-SYMLINK`` — a tracked symlink resolves to a path that is not tracked.
* ``LOCAL-DIR`` — a tracked path has a component named ``out``, ``_work``,
  ``_ephemeral``, ``.doc-quarantine``, ``experiments`` or ``.agent-tasks``.

No allow-list applies: each finding is fixed by ``git rm --cached`` (after the
content has been moved to the local working store) or by correcting the rule.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release_audit import ROOT, Finding, git, report, tracked_files  # noqa: E402

LOCAL_DIR_NAMES = frozenset(
    {"out", "_work", "_ephemeral", ".doc-quarantine", "experiments", ".agent-tasks"}
)


def tracked_ignored(root: Path) -> list[Finding]:
    """Tracked files that match an ignore rule."""
    out = git("ls-files", "-i", "-c", "--exclude-standard", root=root)
    return [
        Finding(
            "TRACKED-IGNORED",
            rel,
            None,
            "tracked file matches an ignore rule",
            "move it to the local store and `git rm --cached` it, or drop the rule",
        )
        for rel in out.splitlines()
        if rel
    ]


def dangling_symlinks(root: Path, tracked: list[str]) -> list[Finding]:
    """Tracked symlinks whose target is not itself tracked."""
    findings: list[Finding] = []
    tracked_set = set(tracked)
    for entry in git("ls-files", "-s", root=root).splitlines():
        if not entry.startswith("120000"):
            continue
        rel = entry.split("\t", 1)[1]
        link = root / rel
        try:
            target = os.readlink(link)
        except OSError:
            target = git("show", f":{rel}", root=root).strip()
        resolved = os.path.normpath(os.path.join(os.path.dirname(rel), target))
        if resolved not in tracked_set:
            findings.append(
                Finding(
                    "DANGLING-SYMLINK",
                    rel,
                    None,
                    f"tracked symlink → {target!r}, which is not tracked",
                    "untrack the link (`git rm --cached`) and keep the target in the local store",
                )
            )
    return findings


def local_dir_paths(tracked: list[str]) -> list[Finding]:
    """Tracked paths inside a directory reserved for local output."""
    return [
        Finding(
            "LOCAL-DIR",
            rel,
            None,
            "tracked under a directory reserved for local output",
            "relocate to the local store and `git rm --cached`",
        )
        for rel in tracked
        if LOCAL_DIR_NAMES.intersection(rel.split("/")[:-1])
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    tracked = tracked_files(args.repo_root)
    findings = (
        tracked_ignored(args.repo_root)
        + dangling_symlinks(args.repo_root, tracked)
        + local_dir_paths(tracked)
    )
    return report(findings, "tracked-ignored gate")


if __name__ == "__main__":
    raise SystemExit(main())
