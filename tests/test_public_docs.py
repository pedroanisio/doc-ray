"""Public-documentation contract tests."""

from __future__ import annotations

from pathlib import Path

import check_public_docs as public_docs

ROOT = Path(__file__).resolve().parents[1]


def test_frontmatter_parser_reads_scalar_fields() -> None:
    text = '---\nstatus: "current"\napplies_to: "1.54.0"\n---\n# Page\n'
    assert public_docs.frontmatter(text)["status"] == "current"
    assert public_docs.frontmatter(text)["applies_to"] == "1.54.0"


def test_all_current_non_release_pages_target_the_canonical_version() -> None:
    problems = public_docs.public_metadata_problems(ROOT)
    assert not problems, "public metadata drift:\n" + "\n".join(problems)


def test_release_note_identity_matches_filename() -> None:
    problems = public_docs.release_note_identity_problems(ROOT)
    assert not problems, "release-note identity drift:\n" + "\n".join(problems)


def test_public_routes_and_relative_links_resolve() -> None:
    problems = public_docs.public_link_problems(ROOT)
    assert not problems, "public link drift:\n" + "\n".join(problems)


def test_public_docs_gate_is_wired_into_local_and_ci_checks() -> None:
    command = "scripts/check_public_docs.py"
    for relative in ("Makefile", ".pre-commit-config.yaml", ".github/workflows/ci.yml"):
        assert command in (ROOT / relative).read_text(encoding="utf-8")
