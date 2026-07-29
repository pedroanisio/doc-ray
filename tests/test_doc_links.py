"""Offline link-gate tests for repository Markdown."""

from __future__ import annotations

from pathlib import Path

import check_doc_links as links

ROOT = Path(__file__).resolve().parents[1]


def test_extract_links_ignores_fenced_examples_and_external_urls() -> None:
    text = "[local](guide.md) [web](https://example.com)\n```markdown\n[example](missing.md)\n```\n"
    assert links.markdown_links(text) == ["guide.md", "https://example.com"]


def test_public_route_maps_to_the_public_doc_tree(tmp_path: Path) -> None:
    target = tmp_path / "docs" / "public" / "concepts" / "sentences.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Sentences\n", encoding="utf-8")
    source = tmp_path / "docs" / "public" / "index.md"
    source.write_text("[Sentences](/docs/concepts/sentences)\n", encoding="utf-8")

    assert links.find_link_problems(tmp_path, [source]) == []


def test_missing_relative_link_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "README.md"
    source.write_text("[Missing](docs/missing.md)\n", encoding="utf-8")

    problems = links.find_link_problems(tmp_path, [source])

    assert len(problems) == 1
    assert "docs/missing.md" in problems[0]


def test_all_tracked_markdown_links_resolve() -> None:
    problems = links.find_link_problems(ROOT, links.tracked_markdown_files(ROOT))
    assert not problems, "unresolved Markdown links:\n" + "\n".join(problems)


def test_link_gate_is_wired_into_local_and_ci_checks() -> None:
    command = "scripts/check_doc_links.py"
    for relative in ("Makefile", ".pre-commit-config.yaml", ".github/workflows/ci.yml"):
        assert command in (ROOT / relative).read_text(encoding="utf-8")
