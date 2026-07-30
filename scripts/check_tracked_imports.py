#!/usr/bin/env python3
"""Fail if a git-tracked module imports a first-party module that is not tracked.

This gate exists because of a concrete production incident: commit 54eb4fc landed
the constituency/SRL wiring — a tracked ``annotation_spacy`` hook, tracked GraphQL
fields, the tracked ``document_syntax`` MCP tool and their tracked tests — while
the three modules that work *imports* (``doc_ray.adapters.annotation_syntax_rows``,
``doc_ray.adapters.persistence_syntax_read``, ``doc_ray.domain.syntax_derived``)
were still **untracked**. The author's working tree had those files on disk, so
local ``pytest`` imported the whole package and passed. Every fresh clone — CI's
``actions/checkout`` and, fatally, the deployed MCP/API image — got only the
tracked files and crashed at import: ``ModuleNotFoundError``. With the server
unable to import, *every* MCP tool call failed, ``search_sentences`` included.

``pytest`` structurally cannot catch this: it imports from the working tree, which
still contains the untracked file. The missing invariant is a property of git's
index, not the filesystem — so it needs an index-aware gate, run in the landing
checklist alongside ``check_file_size.py`` / ``check_version_consistency.py``.

The invariant: for every first-party import in a **tracked** ``.py`` file, the
module it resolves to must itself be a **tracked** file. A committed module may
never depend on an uncommitted one.

The check is deliberately conservative — it only flags an import that resolves to
a file which exists on disk but is untracked, or to a first-party module path that
has no file at all (a dangling import). ``from pkg import name`` where ``name`` is
a symbol re-exported by a tracked ``pkg`` is fine: only names that correspond to an
actual submodule file are checked. That keeps the gate free of false positives on
ordinary symbol imports while still catching an untracked submodule imported by
name — the exact shape of the 54eb4fc regression.

Exit 0 when clean, 1 on any finding.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

#: A single finding: importing file, line, the offending module, and why.
Finding = tuple[str, int, str, str]


def _tracked_files(root: Path) -> set[str]:
    """Return every git-tracked path under *root*, repo-relative with POSIX seps."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {entry for entry in out.split("\0") if entry}


def first_party_roots(tracked: set[str]) -> dict[str, str]:
    """Map each importable top-level package name to its tracked base directory.

    A package is any top-level directory (at the repo root or directly under
    ``src/``) whose ``__init__.py`` is tracked. Derived from the index rather than
    hardcoded so a new top-level package is covered the day it is committed.
    """
    roots: dict[str, str] = {}
    for path in tracked:
        parts = path.split("/")
        if parts[-1] != "__init__.py":
            continue
        if len(parts) == 2:  # <pkg>/__init__.py
            roots.setdefault(parts[0], parts[0])
        elif len(parts) == 3 and parts[0] == "src":  # src/<pkg>/__init__.py
            roots.setdefault(parts[1], f"src/{parts[1]}")
    return roots


@dataclass(frozen=True)
class Resolver:
    """Resolves first-party dotted names against one tree's index and disk.

    Bundling the three inputs (repo root, tracked set, package roots) makes the
    resolution logic pure with respect to its arguments, so the gate is unit
    testable against a synthetic tree without touching the real repository.
    """

    root: Path
    tracked: frozenset[str]
    roots: dict[str, str]

    @classmethod
    def for_tree(cls, root: Path) -> Resolver:
        tracked = _tracked_files(root)
        return cls(root=root, tracked=frozenset(tracked), roots=first_party_roots(tracked))

    def _candidates(self, dotted: str) -> list[str] | None:
        """Repo-relative files a first-party *dotted* module could resolve to.

        Returns ``None`` when *dotted* is not first-party (a stdlib or third-party
        import this gate has no opinion on): the module-file candidate first, then
        the package-``__init__`` candidate.
        """
        parts = dotted.split(".")
        base = self.roots.get(parts[0])
        if base is None:
            return None
        stem = "/".join([base, *parts[1:]]) if parts[1:] else base
        return [f"{stem}.py", f"{stem}/__init__.py"]

    def resolution(self, dotted: str) -> str:
        """Classify how a first-party module resolves against the index and disk.

        One of ``"external"`` (not first-party), ``"tracked"``, ``"untracked"`` (a
        candidate file exists on disk but none is tracked), or ``"absent"`` (no
        candidate file exists at all).
        """
        candidates = self._candidates(dotted)
        if candidates is None:
            return "external"
        if any(candidate in self.tracked for candidate in candidates):
            return "tracked"
        if any((self.root / candidate).exists() for candidate in candidates):
            return "untracked"
        return "absent"

    def package_of(self, file_rel: str) -> str | None:
        """Return the dotted package a tracked file belongs to, or ``None``."""
        for name, base in self.roots.items():
            prefix = f"{base}/"
            if file_rel.startswith(prefix):
                inner = file_rel[len(prefix) :].split("/")
                # Drop the filename; a package's own modules share its dotted path.
                return ".".join([name, *inner[:-1]]) if len(inner) > 1 else name
        return None


def _module_of(node: ast.ImportFrom, file_rel: str, resolver: Resolver) -> str | None:
    """Resolve an ``ImportFrom`` (absolute or relative) to a first-party dotted path.

    Relative imports are anchored to the importing file's package, itself derived
    from the file's location under a first-party root. Returns ``None`` when the
    import cannot be a first-party module (third-party absolute, or a relative
    import from a file outside every first-party root).
    """
    if node.level == 0:
        return node.module
    package = resolver.package_of(file_rel)
    if package is None:
        return None
    ancestor = package.split(".")
    # level 1 = the file's own package; each extra level climbs one parent.
    climb = node.level - 1
    if climb > len(ancestor):
        return None
    base = ancestor[: len(ancestor) - climb]
    tail = node.module.split(".") if node.module else []
    combined = [*base, *tail]
    return ".".join(combined) if combined else None


def _check_file(file_rel: str, resolver: Resolver) -> list[Finding]:
    """Return findings for one tracked file."""
    try:
        tree = ast.parse((resolver.root / file_rel).read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []  # ruff/format own syntax; a read error is not this gate's call.

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _record_module(file_rel, alias.name, node.lineno, resolver, findings)
        elif isinstance(node, ast.ImportFrom):
            _check_from(node, file_rel, resolver, findings)
    return findings


def _check_from(
    node: ast.ImportFrom, file_rel: str, resolver: Resolver, findings: list[Finding]
) -> None:
    """Flag an untracked module, or an untracked submodule imported by name."""
    module = _module_of(node, file_rel, resolver)
    if module is None:
        return
    state = resolver.resolution(module)
    if state == "external":
        return
    if state in ("untracked", "absent"):
        _record_module(file_rel, module, node.lineno, resolver, findings)
        return
    # The module package is tracked; a name it imports may still be an untracked
    # *submodule* (the `from pkg import submodule` incident shape). `absent` here
    # is silent: the name is an ordinary re-exported symbol, not a module file.
    for alias in node.names:
        if alias.name == "*":
            continue
        dotted = f"{module}.{alias.name}"
        if resolver.resolution(dotted) == "untracked":
            findings.append(
                (file_rel, node.lineno, dotted, "imported name is an untracked submodule")
            )


def _record_module(
    file_rel: str, dotted: str, line: int, resolver: Resolver, findings: list[Finding]
) -> None:
    """Record a finding when a directly imported first-party module is not tracked."""
    state = resolver.resolution(dotted)
    if state == "untracked":
        findings.append((file_rel, line, dotted, "resolves to an untracked file"))
    elif state == "absent":
        findings.append((file_rel, line, dotted, "first-party module has no tracked file"))


def find_violations(pathspec: tuple[str, ...] = (), *, root: Path = _ROOT) -> list[Finding]:
    """Return ``(file, line, module, reason)`` for every untracked-import finding."""
    resolver = Resolver.for_tree(root)
    scanned = sorted(
        path
        for path in resolver.tracked
        if path.endswith(".py") and (not pathspec or any(path.startswith(p) for p in pathspec))
    )
    violations: list[Finding] = []
    for file_rel in scanned:
        violations.extend(_check_file(file_rel, resolver))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        help="Optional repo-relative path prefixes to restrict the scan (default: all).",
    )
    args = parser.parse_args()

    violations = find_violations(tuple(args.roots))
    if not violations:
        print("tracked-imports OK: every first-party import resolves to a tracked module.")
        return 0

    print("TRACKED-IMPORTS VIOLATION: a committed module imports an uncommitted one.")
    print("A fresh clone (CI, the deployed image) would crash at import. `git add` the")
    print("missing module(s), or drop the import.\n")
    for file_rel, line, module, reason in violations:
        print(f"  {file_rel}:{line}  imports '{module}' — {reason}")
    print(f"\n{len(violations)} finding(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
