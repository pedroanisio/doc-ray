#!/usr/bin/env python3
"""Release-audit gate C-2: tracked files must not point at private material.

Each rule pairs a pattern with the place it applies, so the gate is not a bare
keyword filter: a date in a test fixture's data is fine, the same date in a
migration comment is a diary entry; ``203.0.113.10`` is documentation space,
``10.13.0.6`` is somebody's VPC.

Rules (``RULE`` → what fails):

* ``PRIV-PATH``  — an absolute home path (``/home/<user>/``, ``/Users/<user>/``,
  ``C:\\Users\\``) anywhere in a tracked text file.
* ``PRIV-STORE`` — a local working-directory name (``_ephemeral``, ``.agent-tasks``,
  ``docs/reports``, ``docs/concept``, the local store path).
* ``PRIV-HOST``  — a managed-database hostname or provider account id, or an
  RFC 1918 address, in any tracked text file.
* ``SESSION``    — session narrative ("this session's …", "operator directive",
  "Agent host session", …) anywhere.
* ``PLAN-LABEL`` — internal plan-phase labels (``Block A``…``G``, ``SLP Block``,
  ``MCP Item N``, ``(P0)``…``(P3)``) in documentation, the changelog or the README.
* ``DIARY``      — a calendar date, or session narrative, inside a comment or
  docstring of source, migrations, templates, scripts or tests.
* ``RULES-REF``  — ``CLAUDE.md`` / ``AGENTS.md`` cited from a comment in source.

Exceptions go in ``.release-audit-allow.txt`` (``RULE<TAB>path[:line]<TAB>reason``).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from re import Pattern

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release_audit import (  # noqa: E402
    ROOT,
    Finding,
    comment_lines,
    is_allowed,
    is_text_candidate,
    load_allow_list,
    read_text,
    report,
    tracked_files,
)

CODE_PREFIXES = ("src/", "db/", "webapp/", "scripts/", "admin-console/src/", "tests/")
DOC_TARGETS = ("docs/", "CHANGELOG.md", "README.md", "CONTRIBUTING.md", "AGENTS.md")

PRIV_PATH = re.compile(
    r"(?<![\w.])/home/(?!you/|user/|<)[A-Za-z][\w.-]*/|(?<![\w.])/Users/(?!you/|<)[\w.-]+/"
    r"|[A-Za-z]:\\Users\\"
)
PRIV_STORE = re.compile(
    r"_ephemeral\b|\.agent-tasks\b|(?<![\w-])docs/reports\b|(?<![\w-])docs/concept\b"
    r"|\.local/share/doc-ray"
)
PRIV_HOST = re.compile(
    r"\bdo-user-\d+\b|\b[\w-]+\.h\.db\.ondigitalocean\.com\b"
    r"|\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b"
    r"|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"
)
SESSION = re.compile(
    r"\bthis session's (?:[\w-]+ )?(?:gap-analysis|audit|findings?|review|pass|analysis"
    r"|measurements?|probe|work)\b|\bthis session (?:found|measured|verified|learned)\b"
    r"|\b(?:found|measured|verified|learned|sampled|confirmed) (?:in|during) this session\b"
    r"|\b(?:verified|measured|undated|sampled) this session\b|Agent host session"
    r"|operator directive|per operator decision|session finding",
    re.IGNORECASE,
)
PLAN_LABEL = re.compile(r"\bSLP Block\b|\bBlock [A-G]\b(?![\w-])|\bMCP Item \d+\b|\(P[0-3]\)")
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
RULES_REF = re.compile(r"\bCLAUDE\.md\b|\bAGENTS\.md\b")

_PLACEHOLDER = re.compile(r"example|REPLACE|<|placeholder", re.IGNORECASE)


def _host_hits(line: str) -> bool:
    return any(not _PLACEHOLDER.search(m.group(0)) for m in PRIV_HOST.finditer(line))


def _scan_lines(
    rel: str, text: str, rule: str, pattern: Pattern[str], remedy: str
) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        hit = _host_hits(line) if rule == "PRIV-HOST" else pattern.search(line) is not None
        if hit:
            findings.append(Finding(rule, rel, lineno, line.strip()[:100], remedy))
    return findings


def scan_file(rel: str, text: str) -> list[Finding]:
    """All findings for one tracked text file."""
    findings = _scan_lines(rel, text, "PRIV-PATH", PRIV_PATH, "use a relative path or an env var")
    findings += _scan_lines(
        rel, text, "PRIV-STORE", PRIV_STORE, "refer to $DOCRAY_WORK_DIR, never to a local directory"
    )
    findings += _scan_lines(
        rel, text, "PRIV-HOST", PRIV_HOST, "use a placeholder host; keep deployment values local"
    )
    findings += _scan_lines(
        rel, text, "SESSION", SESSION, "state the fact or invariant; drop the session narrative"
    )
    if rel.startswith(DOC_TARGETS):
        findings += _scan_lines(
            rel, text, "PLAN-LABEL", PLAN_LABEL, "name the feature, not the plan phase"
        )
    if rel.startswith(CODE_PREFIXES):
        for lineno, comment in sorted(comment_lines(rel, text).items()):
            if DATE.search(comment) or SESSION.search(comment):
                findings.append(
                    Finding(
                        "DIARY",
                        rel,
                        lineno,
                        comment.strip()[:100],
                        "keep the failure mode and the invariant; drop the date, count and decider",
                    )
                )
            if RULES_REF.search(comment):
                findings.append(
                    Finding(
                        "RULES-REF",
                        rel,
                        lineno,
                        comment.strip()[:100],
                        "state the rule in place (or cite CONTRIBUTING.md)",
                    )
                )
    return findings


def run(root: Path = ROOT, paths: list[str] | None = None) -> list[Finding]:
    """Scan *paths* (default: every tracked text file) and drop allow-listed findings."""
    allowed = load_allow_list(root / ".release-audit-allow.txt")
    candidates = paths if paths is not None else tracked_files(root)
    findings: list[Finding] = []
    for rel in candidates:
        if not is_text_candidate(rel):
            continue
        text = read_text(rel, root)
        if text is None:
            continue
        findings.extend(f for f in scan_file(rel, text) if not is_allowed(f, allowed))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Run the gate over the tracked tree (or the paths given, e.g. by pre-commit)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="limit the scan to these repo-relative paths")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    rel_paths = None
    if args.paths:
        tracked = set(tracked_files(args.repo_root))
        rel_paths = [
            str(Path(p).resolve().relative_to(args.repo_root.resolve()))
            if Path(p).is_absolute()
            else p
            for p in args.paths
        ]
        rel_paths = [p for p in rel_paths if p in tracked]
    return report(run(args.repo_root, rel_paths), "private-references gate")


if __name__ == "__main__":
    raise SystemExit(main())
