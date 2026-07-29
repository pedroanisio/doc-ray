"""Release version and annotated-tag alignment tests."""

from __future__ import annotations

from pathlib import Path

import check_release_state as release_state

ROOT = Path(__file__).resolve().parents[1]


def test_latest_semver_tag_uses_numeric_order() -> None:
    assert release_state.latest_semver_tag(["v1.9.0", "v1.10.0", "junk"]) == "v1.10.0"


def test_repository_release_tag_matches_canonical_version() -> None:
    problems = release_state.release_problems(ROOT)
    assert not problems, "release-state drift:\n" + "\n".join(problems)


def test_release_gate_is_wired_with_full_tag_history() -> None:
    command = "scripts/check_release_state.py"
    for relative in ("Makefile", ".pre-commit-config.yaml", ".github/workflows/ci.yml"):
        assert command in (ROOT / relative).read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in ci
