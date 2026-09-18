#!/usr/bin/env python3
"""Release-audit gate C-7: no credentials, private keys or oversized files in the index.

Runs over every tracked file, or over the paths pre-commit hands it. It is a
local, dependency-free complement to a hosted secret scanner, not a
replacement: it knows the token shapes this deployment can plausibly leak.

* ``SECRET-KEY-BLOCK`` — a PEM private-key header.
* ``SECRET-TOKEN`` — a provider token shape (Anthropic, OpenAI, GitHub, AWS,
  DigitalOcean, Slack) or a JWT.
* ``SECRET-ENV-VALUE`` — a non-empty secret-looking assignment in a tracked
  env file that is not a ``*.example`` sample.
* ``FILE-TOO-LARGE`` — a tracked file over 5 MB outside ``static/fixtures/``.
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
    is_text_candidate,
    load_allow_list,
    read_text,
    report,
    tracked_files,
)

MAX_BYTES = 5 * 1024 * 1024
KEY_BLOCK = re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----")
TOKEN = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|AKIA[0-9A-Z]{16}|dop_v1_[0-9a-f]{40,}|xox[abp]-[0-9A-Za-z-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"
)
ENV_FILE = re.compile(r"(^|/)\.env(\.[\w.-]+)?$")
SECRET_KEY_NAME = re.compile(
    r"(PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY|PRIVATE_KEY)S?=", re.IGNORECASE
)
PLACEHOLDER = re.compile(
    r"^(?:|\s*#.*|<[^>]*>|change[-_ ]?me|replace[-_ ]?me|example|xxx+|\*+|docray|minioadmin)$",
    re.IGNORECASE,
)


def scan_text(rel: str, text: str) -> list[Finding]:
    """Token, key-block and env-value findings for one text file."""
    findings: list[Finding] = []
    is_env = bool(ENV_FILE.search(rel)) and not rel.endswith(".example")
    for lineno, line in enumerate(text.splitlines(), 1):
        if KEY_BLOCK.search(line):
            findings.append(
                Finding(
                    "SECRET-KEY-BLOCK", rel, lineno, "private key material", "remove and rotate"
                )
            )
        if TOKEN.search(line):
            findings.append(
                Finding("SECRET-TOKEN", rel, lineno, "provider token shape", "remove and rotate")
            )
        if is_env and SECRET_KEY_NAME.search(line) and "=" in line:
            value = line.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'")
            if not PLACEHOLDER.match(value):
                findings.append(
                    Finding(
                        "SECRET-ENV-VALUE",
                        rel,
                        lineno,
                        line.split("=", 1)[0].strip() + "=…",
                        "env files with real values are never tracked; ship a *.example",
                    )
                )
    return findings


def run(root: Path = ROOT, paths: list[str] | None = None) -> list[Finding]:
    """Scan the tracked tree (or the given paths)."""
    allowed = load_allow_list(root / ".release-audit-allow.txt")
    candidates = paths if paths is not None else tracked_files(root)
    findings: list[Finding] = []
    for rel in candidates:
        full = root / rel
        if not full.is_file():
            continue
        if full.stat().st_size > MAX_BYTES and not rel.startswith("static/fixtures/"):
            findings.append(
                Finding(
                    "FILE-TOO-LARGE",
                    rel,
                    None,
                    f"{full.stat().st_size // (1024 * 1024)} MB",
                    "large binaries belong in object storage or static/fixtures/",
                )
            )
        if is_text_candidate(rel):
            text = read_text(rel, root)
            if text is not None:
                findings.extend(scan_text(rel, text))
    return [f for f in findings if not is_allowed(f, allowed)]


def main(argv: list[str] | None = None) -> int:
    """Run the gate over the tracked tree or the given paths."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    rel_paths = None
    if args.paths:
        root = args.repo_root.resolve()
        rel_paths = [
            str(Path(p).resolve().relative_to(root)) if Path(p).is_absolute() else p
            for p in args.paths
        ]
    return report(run(args.repo_root, rel_paths), "secrets gate")


if __name__ == "__main__":
    raise SystemExit(main())
