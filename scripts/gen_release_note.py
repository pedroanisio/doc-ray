#!/usr/bin/env python3
"""Generate a public release-note stub for one version from CHANGELOG.md.

Usage:
    python scripts/gen_release_note.py 1.49.0

Writes ``docs/public/release-notes/<version>.md`` containing the public-notes
frontmatter and the raw CHANGELOG section as a fenced draft to distill into
public voice (no internal codenames, per docs/documentation-governance.md).
Refuses to overwrite an existing note. Reminds you to add the index row —
``scripts/check_version_consistency.py`` fails until both exist for the
current version.
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_NOTES = _ROOT / "docs" / "public" / "release-notes"

_TEMPLATE = """---
disclaimer:
  notice: >-
    No information within this document should be taken for granted.
    Any statement or premise not backed by a real logical definition
    or verifiable reference may be invalid, erroneous, or a hallucination.
  generated_by: "scripts/gen_release_note.py (stub) — distilled by <author/model>"
  date: "{today}"
title: "Doc-Ray {version} release notes"
doc_type: "release-note"
audience: ["end-user", "administrator", "integrator"]
surfaces: ["webapp", "api", "mcp"]
classification: "public"
applies_to: "{version}"
owner: "documentation-maintainer"
last_reviewed: "{today}"
status: "current"
---

# Doc-Ray {version}

<!-- TODO: one-sentence public summary. Distill the draft below into public
     voice: who is affected, what to know, action required. Remove internal
     codenames, decision ids, and backlog ids (documentation-governance.md §5).
     Delete the draft block when done. -->

## Who is affected

- **TODO**

## Action required

None. <!-- or state it -->

<!-- DRAFT — raw CHANGELOG section, delete after distilling:
{section}
-->
"""


def changelog_section(version: str) -> str:
    text = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith(f"## [{version}"):
            capturing = True
            continue
        if capturing and line.startswith("## ["):
            break
        if capturing:
            out.append(line)
    if not out:
        raise SystemExit(f"fatal: no '## [{version}]' section in CHANGELOG.md")
    return "\n".join(out).strip().replace("-->", "-- >")


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        print(__doc__, file=sys.stderr)
        return 2
    version = sys.argv[1]
    target = _NOTES / f"{version}.md"
    if target.exists():
        raise SystemExit(f"fatal: {target} already exists; refusing to overwrite")
    today = datetime.date.today().isoformat()
    target.write_text(
        _TEMPLATE.format(version=version, today=today, section=changelog_section(version)),
        encoding="utf-8",
    )
    print(f"wrote stub {target}")
    print(
        "next: distill the draft into public voice AND add the newest-first row to "
        f"{_NOTES / 'index.md'} — check_version_consistency.py requires both."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
