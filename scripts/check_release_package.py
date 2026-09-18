#!/usr/bin/env python3
"""Release-audit gate C-5: inspect what a release actually ships.

Builds the wheel and sdist into ``$DOCRAY_WORK_DIR/release-check`` (or
``_work/release-check``) and lists their contents; with ``--image`` it also
lists the files inside a built image. Nothing is uploaded or tagged.

* ``PKG-TESTS`` — the wheel or sdist carries ``doc_ray/tests``.
* ``PKG-STRAY-DOC`` — the sdist carries Markdown other than README/CHANGELOG/LICENSE.
* ``IMAGE-TREE`` — the image holds files under ``/app`` outside ``src/``, ``db/``,
  ``webapp/`` (or ``src/doc_ray/tests`` when ``--no-tests-in-image`` is given).
* ``IMAGE-PRIVATE`` — a file in the image matches the private-reference rules.

Requires the ``build`` package (``pip install build``) and, for ``--image``, a
Docker CLI that can run the image.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _release_audit import ROOT, Finding, report  # noqa: E402
from _work_dir import work_dir  # noqa: E402
from check_private_references import scan_file  # noqa: E402

ALLOWED_SDIST_DOCS = frozenset({"README.md", "CHANGELOG.md", "LICENSE", "LICENSE.md"})
IMAGE_ALLOWED_PREFIXES = ("/app/src/", "/app/db/", "/app/webapp/")


def build_distributions(root: Path, out: Path) -> tuple[Path, Path]:
    """Build wheel + sdist into *out* and return their paths."""
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(out), str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(out.glob("*.whl"))
    sdist = next(out.glob("*.tar.gz"))
    return wheel, sdist


def inspect_wheel(wheel: Path) -> list[Finding]:
    """The wheel must not carry the test suite."""
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    return [
        Finding("PKG-TESTS", wheel.name, None, name, "exclude doc_ray.tests from packages.find")
        for name in names
        if name.startswith("doc_ray/tests/")
    ][:1]


def inspect_sdist(sdist: Path) -> list[Finding]:
    """The sdist must not carry the test suite or stray documents."""
    findings: list[Finding] = []
    with tarfile.open(sdist) as tf:
        names = [m.name.split("/", 1)[1] for m in tf.getmembers() if "/" in m.name]
    if any(n.startswith("src/doc_ray/tests/") for n in names):
        findings.append(
            Finding("PKG-TESTS", sdist.name, None, "src/doc_ray/tests/", "exclude from the sdist")
        )
    stray = sorted(n for n in names if n.endswith(".md") and Path(n).name not in ALLOWED_SDIST_DOCS)
    for name in stray:
        findings.append(
            Finding(
                "PKG-STRAY-DOC",
                sdist.name,
                None,
                name,
                "documents ship from docs/public, not the sdist",
            )
        )
    return findings


def inspect_image(tag: str, tests_allowed: bool) -> list[Finding]:
    """List the image's /app tree and apply the tree and private-reference rules."""
    listing = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", tag, "-c", "find /app -type f | sort"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    findings: list[Finding] = []
    for path in listing:
        if not path.startswith(IMAGE_ALLOWED_PREFIXES):
            findings.append(
                Finding("IMAGE-TREE", tag, None, path, "the image ships only src/, db/ and webapp/")
            )
        elif not tests_allowed and path.startswith("/app/src/doc_ray/tests/"):
            findings.append(
                Finding(
                    "IMAGE-TREE", tag, None, path, "strip the test suite from the runtime image"
                )
            )
    text_paths = [
        p
        for p in listing
        if p.endswith((".py", ".sql", ".html", ".css", ".js", ".md", ".txt", ".yaml", ".yml"))
    ]
    if text_paths:
        script = (
            "for f in "
            + " ".join(f"'{p}'" for p in text_paths)
            + '; do printf "\\0%s\\0" "$f"; cat "$f"; done'
        )
        blob = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "sh", tag, "-c", script],
            check=True,
            capture_output=True,
        ).stdout.decode("utf-8", errors="replace")
        for rel_path, content in _pairs(blob):
            rel = rel_path.removeprefix("/app/")
            findings.extend(
                Finding("IMAGE-PRIVATE", tag, f.line, f"{rel}: {f.message}", f.remedy)
                for f in scan_file(rel, content)
            )
    return findings


def _pairs(blob: str) -> list[tuple[str, str]]:
    parts = blob.split("\0")
    # layout: "", path1, content1, path2, content2, ...
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def main(argv: list[str] | None = None) -> int:
    """Build the distributions, optionally inspect an image, and report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--image", help="tag of a locally built image to inspect")
    parser.add_argument(
        "--no-tests-in-image",
        action="store_true",
        help="also fail when the runtime image carries src/doc_ray/tests",
    )
    parser.add_argument("--skip-build", action="store_true", help="inspect only the image")
    args = parser.parse_args(argv)
    findings: list[Finding] = []
    if not args.skip_build:
        out = work_dir("release-check")
        for stale in out.glob("*"):
            stale.unlink()
        wheel, sdist = build_distributions(args.repo_root, out)
        findings += inspect_wheel(wheel) + inspect_sdist(sdist)
    if args.image:
        findings += inspect_image(args.image, tests_allowed=not args.no_tests_in_image)
    return report(findings, "release-package gate")


if __name__ == "__main__":
    raise SystemExit(main())
