#!/usr/bin/env python3
"""Check repository Markdown links without making network requests.

Relative file links are resolved from the source document. Public site routes
under ``/docs/`` are resolved against ``docs/public/`` using the same
``<slug>.md`` or ``<slug>/index.md`` convention as the published tree.
Application routes and external URLs are deliberately ignored.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote, urlsplit

_ROOT = Path(__file__).resolve().parent.parent
_INLINE_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+['\"(][^\n]*?)?\s*\)")
_REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(?:<([^>]+)>|(\S+))", re.MULTILINE)
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_EXTERNAL_SCHEMES = frozenset({"data", "ftp", "http", "https", "mailto", "tel"})


def _prose_without_fenced_code(text: str) -> str:
    """Return Markdown prose with fenced code blocks removed."""
    kept: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)[0]
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is None:
            kept.append(line)
    return "\n".join(kept)


def markdown_links(text: str) -> list[str]:
    """Extract inline and reference-definition destinations from Markdown prose."""
    prose = _prose_without_fenced_code(text)
    links = [a or b for a, b in _INLINE_LINK_RE.findall(prose)]
    links.extend(a or b for a, b in _REFERENCE_LINK_RE.findall(prose))
    return links


def tracked_markdown_files(root: Path) -> list[Path]:
    """Return tracked Markdown files, with a filesystem fallback outside Git."""
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                "*.md",
            ],
            cwd=root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        excluded = {".git", ".venv", "node_modules", "_work"}
        return sorted(
            path
            for path in root.rglob("*.md")
            if not excluded.intersection(path.relative_to(root).parts)
        )
    return [
        path
        for raw in result.stdout.split(b"\0")
        if raw and (path := root / raw.decode()).is_file()
    ]


def _public_route_candidates(root: Path, route: str) -> tuple[Path, ...]:
    relative = unquote(route.removeprefix("/docs/").strip("/"))
    base = root / "docs" / "public" / relative
    if base.suffix == ".md":
        return (base,)
    return (base.with_suffix(".md"), base / "index.md")


def _local_candidates(root: Path, source: Path, target: str) -> tuple[Path, ...]:
    split = urlsplit(target)
    if split.scheme.lower() in _EXTERNAL_SCHEMES or target.startswith("//"):
        return ()
    path_text = unquote(split.path)
    if not path_text or path_text.startswith(("#", "{", "$")):
        return ()
    if path_text.startswith("/docs/"):
        return _public_route_candidates(root, path_text)
    if path_text.startswith("/"):
        return ()  # application route, not a repository file
    if any(token in path_text for token in ("<", ">", "*")):
        return ()  # documented placeholder or glob
    return ((source.parent / path_text).resolve(),)


def find_link_problems(root: Path, paths: Iterable[Path]) -> list[str]:
    """Return deterministic diagnostics for unresolved local Markdown links."""
    root = root.resolve()
    problems: list[str] = []
    for source in sorted({path.resolve() for path in paths}):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            problems.append(f"{source}: cannot read Markdown: {exc}")
            continue
        for target in markdown_links(text):
            candidates = _local_candidates(root, source, target)
            if candidates and not any(candidate.exists() for candidate in candidates):
                try:
                    label = source.relative_to(root)
                except ValueError:
                    label = source
                expected = " or ".join(str(path) for path in candidates)
                problems.append(f"{label}: `{target}` does not resolve ({expected})")
    return sorted(set(problems))


def main(argv: list[str] | None = None) -> int:
    """Run the offline link gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Markdown files to check")
    args = parser.parse_args(argv)
    paths = [(_ROOT / path if not path.is_absolute() else path) for path in args.paths]
    problems = find_link_problems(_ROOT, paths or tracked_markdown_files(_ROOT))
    if problems:
        print("Markdown link drift:", file=sys.stderr)
        print("\n".join(f"  - {problem}" for problem in problems), file=sys.stderr)
        return 1
    print("Markdown links OK: all local files and /docs routes resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
