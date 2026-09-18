#!/usr/bin/env python3
"""Release-audit gate C-8: the front-door documents agree with the tree.

* ``ADR-RANGE`` — ``README.md`` states an ADR range that does not end at the
  highest ``D-NNN`` in ``engineering_decisions.yml``.
* ``GATE-LIST`` — a ``scripts/check_*.py`` (or ``gen_*.py --check``) hook in
  ``.pre-commit-config.yaml`` is missing from ``CONTRIBUTING.md``'s landing
  checklist, or the README's quality-gates section does not point at it.
* ``FENCE`` — a Markdown file has an unmatched code fence.
* ``PROVENANCE`` — a ``generated_by`` value is not a plain model/tool identifier
  (it carries a dash-separated narrative, "revised", "merge", "Block", …).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release_audit import (  # noqa: E402
    ROOT,
    Finding,
    is_allowed,
    load_allow_list,
    report,
    tracked_files,
)

DECISION_ID = re.compile(r"^\s*-?\s*id:\s*\"?(D-\d{3})\"?", re.MULTILINE)
README_RANGE = re.compile(r"runs through \*\*(D-\d{3})\*\*")
HOOK_SCRIPT = re.compile(r"scripts/(check_[a-z_]+\.py|gen_[a-z_]+\.py)")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})", re.MULTILINE)
GENERATED_BY = re.compile(r"^\s*generated_by:\s*\"?(.*?)\"?\s*$", re.MULTILINE)
NARRATIVE = re.compile(
    r" — |\brevised\b|\bmerge\b|\bBlock [A-G]\b|\bsession\b|\bdraft\b", re.IGNORECASE
)


def adr_range(readme: str, registry: str) -> list[Finding]:
    """README's stated ADR range must end at the registry's highest id."""
    ids = sorted(DECISION_ID.findall(registry))
    stated = README_RANGE.search(readme)
    if not ids or not stated:
        return []
    if stated.group(1) != ids[-1]:
        return [
            Finding(
                "ADR-RANGE",
                "README.md",
                readme.count("\n", 0, stated.start()) + 1,
                f"says the ADR set runs through {stated.group(1)}, registry ends at {ids[-1]}",
                "update the README",
            )
        ]
    return []


def gate_list(precommit: str, contributing: str, readme: str) -> list[Finding]:
    """Every hook script must be named in the landing checklist."""
    findings: list[Finding] = []
    for script in sorted(set(HOOK_SCRIPT.findall(precommit))):
        if script not in contributing:
            findings.append(
                Finding(
                    "GATE-LIST",
                    "CONTRIBUTING.md",
                    None,
                    f"pre-commit runs scripts/{script} but the landing checklist does not list it",
                    "add it to the checklist",
                )
            )
    if "CONTRIBUTING.md" not in readme:
        findings.append(
            Finding(
                "GATE-LIST",
                "README.md",
                None,
                "does not point at CONTRIBUTING.md",
                "link it from the quality-gates section",
            )
        )
    return findings


def fences(rel: str, text: str) -> list[Finding]:
    """An odd number of fence lines means a fence was left open."""
    if len(FENCE.findall(text)) % 2:
        return [
            Finding("FENCE", rel, None, "unmatched code fence", "close or remove the stray fence")
        ]
    return []


def provenance(rel: str, text: str) -> list[Finding]:
    """``generated_by`` names a model or tool, nothing more."""
    findings: list[Finding] = []
    for match in GENERATED_BY.finditer(text):
        value = match.group(1)
        if NARRATIVE.search(value) or len(value) > 90:
            findings.append(
                Finding(
                    "PROVENANCE",
                    rel,
                    text.count("\n", 0, match.start()) + 1,
                    f"generated_by: {value[:60]!r}",
                    "state the model/tool only; history belongs in git",
                )
            )
    return findings


def run(root: Path = ROOT) -> list[Finding]:
    """Run every front-door check."""
    allowed = load_allow_list(root / ".release-audit-allow.txt")
    readme = (root / "README.md").read_text(encoding="utf-8")
    registry = (root / "engineering_decisions.yml").read_text(encoding="utf-8")
    precommit = (root / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    contributing = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    findings = adr_range(readme, registry) + gate_list(precommit, contributing, readme)
    for rel in tracked_files(root):
        if not rel.endswith(".md"):
            continue
        text = (root / rel).read_text(encoding="utf-8")
        findings.extend(fences(rel, text))
        findings.extend(provenance(rel, text))
    return [f for f in findings if not is_allowed(f, allowed)]


def main(argv: list[str] | None = None) -> int:
    """Run the gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    return report(run(args.repo_root), "front-door gate")


if __name__ == "__main__":
    raise SystemExit(main())
