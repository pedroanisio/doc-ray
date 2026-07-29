"""Quarantine ignore-policy regression tests."""

from __future__ import annotations

from pathlib import Path

import check_doc_quarantine_policy as policy

ROOT = Path(__file__).resolve().parents[1]


def test_policy_rejects_conflict_markers_and_missing_rules(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "<<<<<<< ours\n.doc-quarantine/\n=======\n>>>>>>> theirs\n",
        encoding="utf-8",
    )

    problems = policy.policy_problems(tmp_path)

    assert any("merge-conflict marker" in problem for problem in problems)
    assert any("missing required ignore rule" in problem for problem in problems)


def test_repository_quarantine_policy_is_unambiguous() -> None:
    assert policy.policy_problems(ROOT) == []


def test_policy_gate_is_wired_into_local_and_ci_checks() -> None:
    command = "scripts/check_doc_quarantine_policy.py"
    for relative in ("Makefile", ".pre-commit-config.yaml", ".github/workflows/ci.yml"):
        assert command in (ROOT / relative).read_text(encoding="utf-8")
