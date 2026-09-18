"""Acceptance guards for webapp-epic operational documentation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_embedding_cutover_runbook_is_linked_from_entrypoint_and_production_guide() -> None:
    """Operators can discover the embedding runbook from both required surfaces."""
    for relative_path in ("README.md", "docs/production-setup.md"):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "runbook-corpus-embeddings.md" in content, relative_path
