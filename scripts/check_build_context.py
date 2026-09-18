#!/usr/bin/env python3
"""Release-audit gate C-4: the Docker build context is an allow-list.

``.dockerignore`` must exclude everything first (``*``) and re-admit only build
and test inputs. The gate evaluates the rules the way the builder does — last
matching rule wins, a directory rule covers its subtree — against a fixed list
of paths that must never reach the daemon, so it holds whether or not those
paths exist on the machine running it.

* ``CONTEXT-NOT-ALLOWLIST`` — the first rule is not ``*``.
* ``CONTEXT-LEAK`` — a forbidden path would be sent to the daemon.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release_audit import ROOT, Finding, report  # noqa: E402

#: Paths that must never be part of the context (existing or hypothetical).
FORBIDDEN = (
    ".env",
    ".env.prod",
    ".env.uat",
    ".env.test",
    ".env.prod.worker",
    ".mcp.json",
    ".git/HEAD",
    ".venv/bin/python",
    ".venv-proto/bin/python",
    ".claude/settings.local.json",
    ".agent-tasks/registry.json",
    "_work/scratch.txt",
    "_ephemeral/notes.md",
    "out/render.png",
    "backups/db.dump",
    "_ingest/file.pdf",
    ".ingest-state/state.json",
    "experiments/poc/run.py",
    ".doc-quarantine/stale/x.pdf",
    "CLAUDE.local.md",
    "pgadmin/servers.json",
    "src/doc_ray/__pycache__/x.pyc",
    "admin-console/node_modules/pkg/index.js",
)

#: Paths the builder needs; the gate fails if the rules stop admitting them.
REQUIRED = (
    "Dockerfile",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "src/doc_ray/__init__.py",
    "db/migrations/001_init.sql",
    "webapp/app.py",
)


def parse_rules(text: str) -> list[str]:
    """Non-comment, non-empty rules in file order."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def _matches(pattern: str, path: str) -> bool:
    negated = pattern.startswith("!")
    pat = pattern[1:] if negated else pattern
    pat = pat.rstrip("/")
    parts = path.split("/")
    if pat.startswith("**/"):
        name = pat[3:]
        return any(fnmatch.fnmatch(part, name) for part in parts)
    if pat == "*":
        return True
    # A directory rule covers the directory and everything under it; a file rule
    # matches the exact relative path (with glob characters honoured).
    if fnmatch.fnmatch(path, pat):
        return True
    return any(fnmatch.fnmatch("/".join(parts[: i + 1]), pat) for i in range(len(parts) - 1))


def is_sent(rules: list[str], path: str) -> bool:
    """Whether *path* would be included in the context under *rules*."""
    sent = True
    for rule in rules:
        if _matches(rule, path):
            sent = rule.startswith("!")
    return sent


def run(root: Path = ROOT) -> list[Finding]:
    """Evaluate ``.dockerignore`` against the forbidden and required path lists."""
    path = root / ".dockerignore"
    if not path.is_file():
        return [Finding("CONTEXT-NOT-ALLOWLIST", ".dockerignore", None, "missing", "create it")]
    rules = parse_rules(path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    if not rules or rules[0] != "*":
        findings.append(
            Finding(
                "CONTEXT-NOT-ALLOWLIST",
                ".dockerignore",
                1,
                "the first rule must be `*`",
                "start with `*` and re-admit build inputs with `!` rules",
            )
        )
    for sample in FORBIDDEN:
        if is_sent(rules, sample):
            findings.append(
                Finding(
                    "CONTEXT-LEAK",
                    ".dockerignore",
                    None,
                    f"{sample} would be sent to the daemon",
                    "do not re-admit it; keep local material outside the context",
                )
            )
    for sample in REQUIRED:
        if not is_sent(rules, sample):
            findings.append(
                Finding(
                    "CONTEXT-LEAK",
                    ".dockerignore",
                    None,
                    f"{sample} is excluded but the build needs it",
                    "re-admit it with a `!` rule",
                )
            )
    return findings


def main(argv: list[str] | None = None) -> int:
    """Run the gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    return report(run(args.repo_root), "build-context gate")


if __name__ == "__main__":
    raise SystemExit(main())
