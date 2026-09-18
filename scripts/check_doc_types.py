#!/usr/bin/env python3
"""Release-audit gate C-3: every tracked Markdown document declares a shippable type.

A tracked ``.md`` file outside ``docs/public/`` (which ``check_public_docs.py``
governs) must carry YAML front matter with ``doc_type`` set to one of the
shippable kinds. Working material declares itself by its type or status, and
that is the signal this gate turns into a refusal:

* ``DOC-TYPE-MISSING`` — no front matter, or no ``doc_type`` in it.
* ``DOC-TYPE-WORKING`` — ``doc_type`` is ``plan``, ``proposal``, ``report``,
  ``finding``, ``session``, ``audit`` or ``brief`` → belongs in ``$DOCRAY_WORK_DIR``.
* ``DOC-STATUS-WORKING`` — ``status`` is ``proposed``, ``draft`` or ``historical``,
  or the file carries a ``retire-when:`` clause.
* ``DOC-TYPE-UNKNOWN`` — ``doc_type`` is not in the shippable set.

Exempt: ``DISCLAIMER.md`` and ``PURPOSE.md`` (fixed operator-owned documents),
the packaged skill under ``skills/`` (its own front-matter contract) and the
GitHub templates under ``.github/``.
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

SHIPPABLE = frozenset(
    {
        "reference",
        "how-to",
        "concept",
        "explanation",
        "runbook",
        "design",
        "policy",
        "changelog",
        "release-note",
        "readme",
    }
)
WORKING_TYPES = frozenset({"plan", "proposal", "report", "finding", "session", "audit", "brief"})
WORKING_STATUS = frozenset({"proposed", "draft", "historical"})
EXEMPT_FILES = frozenset({"DISCLAIMER.md", "PURPOSE.md"})
EXEMPT_PREFIXES = ("docs/public/", "skills/", ".github/")

_FIELD_RE = re.compile(r"^([A-Za-z_][\w-]*):\s*(.*?)\s*$")


def front_matter(text: str) -> dict[str, str] | None:
    """Top-level scalar fields of the leading YAML block, or ``None`` when absent."""
    if not text.startswith("---\n"):
        return None
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return None
    fields: dict[str, str] = {}
    for line in text[4:closing].splitlines():
        match = _FIELD_RE.match(line)
        if match:
            fields[match.group(1)] = match.group(2).strip("\"'")
    return fields


def check_document(rel: str, text: str) -> list[Finding]:
    """Findings for one Markdown document."""
    fields = front_matter(text)
    remedy_store = "move it to $DOCRAY_WORK_DIR or retype it as a shippable document"
    if fields is None or "doc_type" not in fields:
        return [
            Finding("DOC-TYPE-MISSING", rel, 1, "no `doc_type` in front matter", "add doc_type")
        ]
    findings: list[Finding] = []
    doc_type = fields["doc_type"].lower()
    if doc_type in WORKING_TYPES:
        findings.append(Finding("DOC-TYPE-WORKING", rel, 1, f"doc_type: {doc_type}", remedy_store))
    elif doc_type not in SHIPPABLE:
        findings.append(
            Finding(
                "DOC-TYPE-UNKNOWN",
                rel,
                1,
                f"doc_type: {doc_type}",
                "use one of " + ", ".join(sorted(SHIPPABLE)),
            )
        )
    status = fields.get("status", "").lower()
    if status in WORKING_STATUS:
        findings.append(Finding("DOC-STATUS-WORKING", rel, 1, f"status: {status}", remedy_store))
    if "retire-when" in fields:
        findings.append(Finding("DOC-STATUS-WORKING", rel, 1, "retire-when: clause", remedy_store))
    return findings


def run(root: Path = ROOT) -> list[Finding]:
    """Scan every tracked Markdown file that the gate governs."""
    allowed = load_allow_list(root / ".release-audit-allow.txt")
    findings: list[Finding] = []
    for rel in tracked_files(root):
        if not rel.endswith(".md") or rel in EXEMPT_FILES or rel.startswith(EXEMPT_PREFIXES):
            continue
        text = (root / rel).read_text(encoding="utf-8")
        findings.extend(f for f in check_document(rel, text) if not is_allowed(f, allowed))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Run the gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    return report(run(args.repo_root), "doc-type gate")


if __name__ == "__main__":
    raise SystemExit(main())
