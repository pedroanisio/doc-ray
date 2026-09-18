#!/usr/bin/env python3
"""Release-audit gate C-6: a commit message describes the change, not the session.

Runs as the ``commit-msg`` hook (``check_commit_message.py <file>``) and in CI over a
range (``--range origin/main..HEAD``). Merge commits are exempt.

* ``COMMIT-FORMAT`` — the subject is not a conventional commit
  (``type(scope)!: summary``, summary at least eight characters).
* ``COMMIT-NARRATIVE`` — the message carries a UUID, the words ``session`` or
  ``checkpoint``, a bare ``sync``, a local store or home path, or an
  ``_ephemeral``/``.agent-tasks`` reference.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release_audit import ROOT, Finding, report  # noqa: E402

SUBJECT = re.compile(
    r"^(feat|fix|refactor|test|docs|chore|ci|build|perf|style|revert)"
    r"(\([a-z0-9._/-]+\))?!?: \S.{7,}$"
)
MERGE = re.compile(r"^Merge |^Revert \"")
NARRATIVE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\bsessions?\b|\bcheckpoint\b|^sync$|/home/[A-Za-z]|/Users/|\.local/share/doc-ray"
    r"|_ephemeral\b|\.agent-tasks\b",
    re.IGNORECASE | re.MULTILINE,
)
#: Words that legitimately contain "session" in this code base (web/MCP sessions).
NARRATIVE_ALLOW = re.compile(
    r"\b(web|mcp|http|oauth|auth|user|browser|db|database|postgres|sql)[ -]sessions?\b"
    r"|\bsession (id|cookie|token|manager|ttl|store|guc)s?\b|session_",
    re.IGNORECASE,
)


def check_message(message: str, where: str) -> list[Finding]:
    """Findings for one commit message (``where`` names the commit or file)."""
    lines = [line for line in message.splitlines() if not line.startswith("#")]
    subject = next((line for line in lines if line.strip()), "")
    if MERGE.match(subject):
        return []
    findings: list[Finding] = []
    if not SUBJECT.match(subject):
        findings.append(
            Finding(
                "COMMIT-FORMAT",
                where,
                1,
                subject[:80] or "(empty subject)",
                "use `type(scope): summary` (feat, fix, refactor, test, docs, chore, ci, build)",
            )
        )
    body = "\n".join(lines)
    scrubbed = NARRATIVE_ALLOW.sub("", body)
    hit = NARRATIVE.search(scrubbed)
    if hit:
        findings.append(
            Finding(
                "COMMIT-NARRATIVE",
                where,
                None,
                f"message contains {hit.group(0)!r}",
                "describe the change; keep session notes in $DOCRAY_WORK_DIR",
            )
        )
    return findings


def _range_messages(spec: str, root: Path) -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%B%x1e", spec],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    messages = []
    for record in out.split("\x1e"):
        if "\x00" in record:
            sha, body = record.strip("\n").split("\x00", 1)
            messages.append((sha[:10], body))
    return messages


def main(argv: list[str] | None = None) -> int:
    """Check a message file (hook) or a commit range (CI)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("message_file", nargs="?", help="path to the commit message (hook mode)")
    parser.add_argument("--range", dest="range_spec", help="git revision range to check (CI mode)")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    findings: list[Finding] = []
    if args.range_spec:
        for sha, body in _range_messages(args.range_spec, args.repo_root):
            findings.extend(check_message(body, sha))
    elif args.message_file:
        findings.extend(
            check_message(Path(args.message_file).read_text(encoding="utf-8"), args.message_file)
        )
    else:
        parser.error("give a message file or --range")
    return report(findings, "commit-message gate")


if __name__ == "__main__":
    raise SystemExit(main())
