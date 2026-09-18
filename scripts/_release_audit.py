"""Shared helpers for the release-audit gates (``scripts/check_tracked_ignored.py``,
``check_private_references.py``, ``check_doc_types.py``, ``check_build_context.py``,
``check_release_package.py``, ``check_commit_message.py``, ``check_secrets.py``,
``check_front_door.py``).

The gates keep releasable engineering assets separate from private working
material. Each gate is deterministic, prints ``path:line  RULE  message → remedy``
for every finding, and honours one allow-list, ``.release-audit-allow.txt`` at
the repository root, whose lines are ``RULE<TAB>path[:line]<TAB>reason``.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_FILE = ROOT / ".release-audit-allow.txt"

#: Files that are never scanned for references: lockfiles, binary fixtures, and
#: the gates themselves (their sources necessarily contain the patterns).
SCAN_SKIP = frozenset(
    {
        "uv.lock",
        "admin-console/package-lock.json",
        ".release-audit-allow.txt",
        "scripts/_release_audit.py",
        "scripts/check_private_references.py",
        "scripts/check_secrets.py",
        "scripts/check_commit_message.py",
        "scripts/check_build_context.py",
        "scripts/check_tracked_ignored.py",
        "scripts/check_doc_types.py",
        "scripts/check_front_door.py",
        "scripts/check_release_package.py",
        "tests/test_release_audit_gates.py",
    }
)

_BINARY_SUFFIXES = frozenset(
    {".pdf", ".epub", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf"}
)


@dataclass(frozen=True)
class Finding:
    """One gate finding."""

    rule: str
    path: str
    line: int | None
    message: str
    remedy: str

    def render(self) -> str:
        """Format as ``path:line  RULE  message → remedy``."""
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}  {self.rule}  {self.message} → {self.remedy}"


def git(*args: str, root: Path = ROOT) -> str:
    """Run a git command in *root* and return stdout."""
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def tracked_files(root: Path = ROOT) -> list[str]:
    """Repo-relative paths of every tracked file."""
    return [line for line in git("ls-files", root=root).splitlines() if line]


def is_text_candidate(rel: str) -> bool:
    """Whether a tracked path should be read as text for scanning."""
    if rel in SCAN_SKIP or rel.startswith("static/fixtures/"):
        return False
    return Path(rel).suffix.lower() not in _BINARY_SUFFIXES


def read_text(rel: str, root: Path = ROOT) -> str | None:
    """Read a tracked file as UTF-8 text, or ``None`` if it is not decodable."""
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def load_allow_list(path: Path = ALLOW_FILE) -> set[tuple[str, str, int | None]]:
    """Parse ``RULE<TAB>path[:line]<TAB>reason`` lines; a missing file allows nothing."""
    allowed: set[tuple[str, str, int | None]] = set()
    if not path.is_file():
        return allowed
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3 or not parts[2].strip():
            raise SystemExit(
                f"{path.name}: every entry needs RULE<TAB>path[:line]<TAB>reason: {raw!r}"
            )
        rule, target = parts[0].strip(), parts[1].strip()
        lineno: int | None = None
        if ":" in target and target.rsplit(":", 1)[1].isdigit():
            target, num = target.rsplit(":", 1)
            lineno = int(num)
        allowed.add((rule, target, lineno))
    return allowed


def is_allowed(finding: Finding, allowed: set[tuple[str, str, int | None]]) -> bool:
    """Whether an allow-list entry covers the finding (whole file or exact line)."""
    return (finding.rule, finding.path, None) in allowed or (
        finding.rule,
        finding.path,
        finding.line,
    ) in allowed


def report(findings: list[Finding], name: str) -> int:
    """Print findings and return the process exit code."""
    if findings:
        print(f"{name}: {len(findings)} finding(s)", file=sys.stderr)
        for f in findings:
            print(f"  {f.render()}", file=sys.stderr)
        return 1
    print(f"{name} OK")
    return 0


# ---------------------------------------------------------------------------
# Comment extraction — the DIARY rule applies to what a maintainer wrote, not to
# data (test timestamps live in string literals and are never comments).
# ---------------------------------------------------------------------------

_LINE_COMMENT = {
    ".sql": "--",
    ".ts": "//",
    ".tsx": "//",
    ".js": "//",
    ".mjs": "//",
    ".sh": "#",
    ".yml": "#",
    ".yaml": "#",
    ".toml": "#",
    ".css": None,
    ".html": None,
    ".ini": "#",
    ".cfg": "#",
    "Makefile": "#",
    "Dockerfile": "#",
}
_BLOCK_COMMENT = {".ts", ".tsx", ".js", ".mjs", ".css", ".sql", ".html"}
_BLOCK_RE = re.compile(r"/\*.*?\*/|<!--.*?-->|\{#.*?#\}", re.DOTALL)


def _python_comment_lines(text: str) -> dict[int, str]:
    lines: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                lines[tok.start[0]] = tok.string
    except (tokenize.TokenError, SyntaxError):
        return {i + 1: t for i, t in enumerate(text.splitlines()) if t.lstrip().startswith("#")}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                value = body[0].value
                if isinstance(value.value, str):
                    for offset, part in enumerate(value.value.splitlines()):
                        lines.setdefault(value.lineno + offset, part)
    return lines


def comment_lines(rel: str, text: str) -> dict[int, str]:
    """Map line number → comment/docstring text for the file's language."""
    suffix = Path(rel).suffix.lower() or Path(rel).name
    if suffix == ".py":
        return _python_comment_lines(text)
    lines: dict[int, str] = {}
    marker = _LINE_COMMENT.get(suffix)
    if marker:
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(marker):
                lines[i] = stripped
            elif marker == "//" and "//" in line and "://" not in line:
                lines[i] = line[line.index("//") :]
    if suffix in _BLOCK_COMMENT or suffix == ".html":
        for match in _BLOCK_RE.finditer(text):
            start_line = text.count("\n", 0, match.start()) + 1
            for offset, part in enumerate(match.group(0).splitlines()):
                lines.setdefault(start_line + offset, part)
    return lines
