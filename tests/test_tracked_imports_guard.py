"""The tracked-imports gate: a committed module may not import an uncommitted one.

Guards the guard (scripts/check_tracked_imports.py). The gate encodes the 54eb4fc
regression as an invariant: that commit imported three modules it never `git add`-ed,
so a fresh clone (CI, the deployed image) crashed at import and every MCP tool call
failed. The tests build throwaway git trees so the resolution logic is exercised
without depending on the repository's own state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_tracked_imports as guard


def test_guard_is_green_on_the_repository():
    assert guard.find_violations() == []


def _init_tree(root: Path, files: dict[str, str], *, track: set[str]) -> None:
    """Write *files* into a fresh git repo and stage only those named in *track*."""
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    for rel in track:
        subprocess.run(["git", "-C", str(root), "add", "--", rel], check=True)


def test_catches_untracked_submodule_imported_by_name(tmp_path):
    """`from pkg import submodule` where the submodule file is on disk but untracked."""
    _init_tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/user.py": "from pkg import helper\n",
            "src/pkg/helper.py": "x = 1\n",  # exists on disk, deliberately NOT staged
        },
        track={"src/pkg/__init__.py", "src/pkg/user.py"},
    )
    modules = {m for _, _, m, _ in guard.find_violations(root=tmp_path)}
    assert "pkg.helper" in modules


def test_catches_untracked_module_imported_directly(tmp_path):
    """`import pkg.helper` where helper.py is present but untracked."""
    _init_tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/user.py": "import pkg.helper\n",
            "src/pkg/helper.py": "x = 1\n",
        },
        track={"src/pkg/__init__.py", "src/pkg/user.py"},
    )
    modules = {m for _, _, m, _ in guard.find_violations(root=tmp_path)}
    assert "pkg.helper" in modules


def test_catches_dangling_first_party_module(tmp_path):
    """A first-party import whose module has no file at all is a dangling import."""
    _init_tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/user.py": "from pkg.ghost import thing\n",  # pkg/ghost.py never existed
        },
        track={"src/pkg/__init__.py", "src/pkg/user.py"},
    )
    findings = guard.find_violations(root=tmp_path)
    assert any(m == "pkg.ghost" and "no tracked file" in reason for _, _, m, reason in findings)


def test_symbol_import_from_tracked_package_is_not_flagged(tmp_path):
    """`from pkg import NAME` where NAME is a re-exported symbol, not a module."""
    _init_tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "CONST = 1\n",
            "src/pkg/user.py": "from pkg import CONST\n",
        },
        track={"src/pkg/__init__.py", "src/pkg/user.py"},
    )
    assert guard.find_violations(root=tmp_path) == []


def test_third_party_and_stdlib_imports_are_ignored(tmp_path):
    _init_tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/user.py": "import os\nimport httpx\nfrom collections import OrderedDict\n",
        },
        track={"src/pkg/__init__.py", "src/pkg/user.py"},
    )
    assert guard.find_violations(root=tmp_path) == []


def test_fully_tracked_tree_is_green(tmp_path):
    _init_tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/user.py": "from pkg import helper\nimport pkg.helper\n",
            "src/pkg/helper.py": "x = 1\n",
        },
        track={"src/pkg/__init__.py", "src/pkg/user.py", "src/pkg/helper.py"},
    )
    assert guard.find_violations(root=tmp_path) == []


def test_relative_import_of_untracked_submodule_is_caught(tmp_path):
    """`from . import helper` resolves against the file's own package."""
    _init_tree(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/sub/__init__.py": "",
            "src/pkg/sub/user.py": "from . import helper\n",
            "src/pkg/sub/helper.py": "x = 1\n",  # untracked
        },
        track={
            "src/pkg/__init__.py",
            "src/pkg/sub/__init__.py",
            "src/pkg/sub/user.py",
        },
    )
    modules = {m for _, _, m, _ in guard.find_violations(root=tmp_path)}
    assert "pkg.sub.helper" in modules
