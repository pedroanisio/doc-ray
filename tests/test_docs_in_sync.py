"""Documentation prose vs machine facts (BL-048), and the drift-gate wiring (BL-047).

DATABASE.md is generated from the live schema and gated against it by
``scripts/gen_data_dictionary.py --check`` (which needs a database). These
tests cover the committed-artifact side without one: the stable,
machine-derivable counts documented in prose must equal what the repository
actually contains, and the drift gate itself must stay wired into both
pre-commit and CI (removing it would silently disarm BL-047).

Scope is deliberately limited to stable facts (BL-048's acceptance
criterion): the migration count only changes when a migration file lands,
never with data or environment.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_database_md_migration_count_matches_migration_files():
    header = (REPO / "docs" / "DATABASE.md").read_text(encoding="utf-8")
    m = re.search(r"(\d+) migrations applied", header)
    assert m, "DATABASE.md no longer documents its 'N migrations applied' count"
    documented = int(m.group(1))
    on_disk = len(list((REPO / "db" / "migrations").glob("*.sql")))
    assert documented == on_disk, (
        f"DATABASE.md documents {documented} migrations but db/migrations/ holds "
        f"{on_disk} — re-run `python scripts/gen_data_dictionary.py`."
    )


def test_database_md_migration_history_lists_every_migration_file():
    text = (REPO / "docs" / "DATABASE.md").read_text(encoding="utf-8")
    missing = [
        p.name
        for p in sorted((REPO / "db" / "migrations").glob("*.sql"))
        if f"`{p.name}`" not in text
    ]
    assert not missing, (
        f"DATABASE.md's migration-history table is missing {missing} — "
        "re-run `python scripts/gen_data_dictionary.py` (and update "
        "scripts/_data_dictionary_content.py's MIGRATIONS list)."
    )


def test_database_md_drift_gate_is_wired_into_pre_commit_and_ci():
    pre_commit = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "gen_data_dictionary.py --check" in pre_commit, (
        ".pre-commit-config.yaml no longer runs the DATABASE.md drift gate (BL-047)"
    )
    assert "gen_data_dictionary.py --check" in ci, (
        "ci.yml no longer runs the DATABASE.md drift gate (BL-047)"
    )
