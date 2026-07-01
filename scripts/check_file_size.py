#!/usr/bin/env python3
"""Enforce the per-file net-lines-of-code cap (engineering decision D-015).

"Net LOC" counts physical lines that carry at least one real code token. Blank
lines, comment-only lines, and string-literal-only lines (module/class/function
docstrings) are excluded — so writing full docstrings, which the project mandates,
never pushes a file over the cap.

Usage::

    python scripts/check_file_size.py src/doc_ray webapp scripts
    python scripts/check_file_size.py --cap 400 --top 10 src/doc_ray

Exit codes: 0 = every file within the cap; 1 = one or more files over the cap;
2 = usage error.
"""

from __future__ import annotations

import argparse
import token as token_mod
import tokenize
from pathlib import Path

DEFAULT_CAP = 400

#: Directory names never descended into while collecting sources.
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".venv-proto",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        ".repo",
    }
)

#: Token types that do not, by themselves, make a physical line "code".
_NON_CODE_TOKENS = frozenset(
    {
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.COMMENT,
        tokenize.STRING,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)

# FSTRING_* tokens exist from Python 3.12; treat f-string text as string-like.
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    _tok = getattr(token_mod, _name, None)
    if _tok is not None:
        _NON_CODE_TOKENS = _NON_CODE_TOKENS | {_tok}


def net_loc(path: Path) -> int:
    """Return the number of physical lines in *path* that carry a code token.

    Falls back to a blank/``#``-comment-stripping count if the file cannot be
    tokenized (e.g. a syntax error), so the checker never crashes on bad input.
    """
    try:
        with path.open("rb") as handle:
            code_lines: set[int] = set()
            for tok in tokenize.tokenize(handle.readline):
                if tok.type in _NON_CODE_TOKENS:
                    continue
                code_lines.add(tok.start[0])
        return len(code_lines)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return _fallback_net_loc(path)


def _fallback_net_loc(path: Path) -> int:
    """Count non-blank, non-comment lines without tokenizing."""
    text = path.read_text(encoding="utf-8", errors="replace")
    total = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            total += 1
    return total


def iter_python_files(*roots: str) -> list[Path]:
    """Yield every ``*.py`` file under *roots*, skipping vendored/cache dirs."""
    found: list[Path] = []
    for root in roots:
        base = Path(root)
        if base.is_file() and base.suffix == ".py":
            found.append(base)
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            found.append(path)
    return found


def find_violations(*roots: str, cap: int = DEFAULT_CAP) -> list[tuple[Path, int]]:
    """Return ``(path, net_loc)`` for every in-scope file exceeding *cap*."""
    return [(path, loc) for path in iter_python_files(*roots) if (loc := net_loc(path)) > cap]


def count_violations(*roots: str, cap: int = DEFAULT_CAP) -> int:
    """Return how many in-scope files exceed *cap* (the D-015 check hook)."""
    return len(find_violations(*roots, cap=cap))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_file_size.py",
        description="Fail if any source file exceeds the net-LOC cap (D-015).",
    )
    parser.add_argument("roots", nargs="+", help="Files or directories to scan.")
    parser.add_argument(
        "--cap",
        type=int,
        default=DEFAULT_CAP,
        help=f"Maximum net LOC per file (default: {DEFAULT_CAP}).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="Also print the N largest in-scope files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: report violations and, optionally, the largest files."""
    args = _build_parser().parse_args(argv)
    violations = find_violations(*args.roots, cap=args.cap)

    if args.top > 0:
        ranked = sorted(
            ((net_loc(p), p) for p in iter_python_files(*args.roots)),
            reverse=True,
        )
        print(f"Top {args.top} files by net LOC:")
        for loc, path in ranked[: args.top]:
            print(f"  {loc:5d}  {path}")

    if violations:
        print(f"D-015 FAIL: {len(violations)} file(s) over {args.cap} net LOC:")
        for path, loc in sorted(violations, key=lambda item: item[1], reverse=True):
            print(f"  {loc:5d}  {path}")
        return 1

    print(f"D-015 OK: all files within {args.cap} net LOC.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
