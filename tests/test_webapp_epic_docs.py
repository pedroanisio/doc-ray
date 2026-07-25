"""Acceptance guards for webapp-epic operational documentation."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_embedding_cutover_runbook_is_linked_from_entrypoint_and_production_guide() -> None:
    """Operators can discover the embedding runbook from both required surfaces."""
    for relative_path in ("README.md", "docs/production-setup.md"):
        content = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "runbook-corpus-embeddings.md" in content, relative_path


def test_surface_parity_guard_is_recorded_in_the_backlog_system_of_record() -> None:
    """BL-020 records the executable GraphQL-to-webapp parity contract."""
    backlog = yaml.safe_load((ROOT / "docs/backlog.yml").read_text(encoding="utf-8"))
    item = next(entry for entry in backlog["items"] if entry["id"] == "BL-020")

    assert item["status"] == "done"
    assert "D-034" in item["related_decisions"]
    assert "scripts/check_surface_parity.py" in item["references"]
    assert "tests/test_surface_parity_guard.py" in item["references"]

    acceptance = " ".join(item["acceptance_criteria"]).lower()
    assert "graphql root field" in acceptance
    assert "webapp" in acceptance
    assert "reasoned exception" in acceptance
