"""Regression guard for the canonical backlog (docs/backlog.yml + docs/BACKLOG.md).

The backlog drifted from the codebase once already: items were left "parked"
after the work they described had actually shipped (jobs queue, GraphQL safety
limits, a lint fix), because nothing re-checked a status claim against the
tree it described. These tests make that drift a test failure instead of a
silent stale document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_BACKLOG_YML = _ROOT / "docs" / "backlog.yml"
_BACKLOG_MD = _ROOT / "docs" / "BACKLOG.md"
_SCHEMA = _ROOT / "docs" / "schema" / "backlog.schema.json"
_DECISIONS = _ROOT / "engineering_decisions.yml"

_BL_ID_RE = re.compile(r"\bBL-\d{3}\b")


def _backlog() -> dict:
    return yaml.safe_load(_BACKLOG_YML.read_text(encoding="utf-8"))


def _schema() -> dict:
    return json.loads(_SCHEMA.read_text(encoding="utf-8"))


def _decisions_by_id() -> dict:
    doc = yaml.safe_load(_DECISIONS.read_text(encoding="utf-8"))
    return {d["id"]: d for d in doc["decisions"]}


def _grep_present(rel_path: str, pattern: str) -> bool:
    """Evaluate a single engineering_decisions.yml grep-kind check against the
    live tree. `rel_path` may be a file or a directory (searched recursively)."""
    target = _ROOT / rel_path
    regex = re.compile(pattern)
    if target.is_dir():
        return any(
            regex.search(p.read_text(encoding="utf-8", errors="ignore"))
            for p in target.rglob("*")
            if p.is_file()
        )
    if not target.exists():
        return False
    return regex.search(target.read_text(encoding="utf-8", errors="ignore")) is not None


def test_backlog_conforms_to_schema():
    jsonschema.validate(_backlog(), _schema())


def test_metadata_total_items_matches_actual_count():
    backlog = _backlog()
    assert backlog["metadata"]["total_items"] == len(backlog["items"])


def test_item_ids_are_unique():
    ids = [item["id"] for item in _backlog()["items"]]
    assert len(ids) == len(set(ids)), "duplicate BL-### id in backlog.yml"


def test_backlog_md_lists_exactly_the_same_ids_as_backlog_yml():
    """docs/BACKLOG.md is a rendered view over docs/backlog.yml (the system of
    record) — the two must never list a different set of items."""
    yaml_ids = {item["id"] for item in _backlog()["items"]}
    md_ids = set(_BL_ID_RE.findall(_BACKLOG_MD.read_text(encoding="utf-8")))
    assert md_ids == yaml_ids


def test_dependencies_reference_real_backlog_ids():
    backlog = _backlog()
    ids = {item["id"] for item in backlog["items"]}
    for item in backlog["items"]:
        for dep in item.get("dependencies", []):
            assert dep in ids, f"{item['id']} depends on unknown {dep}"


def test_related_decisions_reference_real_decision_ids():
    backlog = _backlog()
    decision_ids = set(_decisions_by_id())
    for item in backlog["items"]:
        for dec_id in item.get("related_decisions", []):
            assert dec_id in decision_ids, f"{item['id']} references unknown {dec_id}"


def test_done_items_grep_backed_claims_still_hold_in_the_tree():
    """A backlog item's status is a claim about the current codebase, not a
    historical note. For every item marked 'done', replay any grep-kind checks
    declared on its related engineering decisions and confirm the tree still
    agrees — this is exactly the check that would have caught BL-009/010/011
    sitting at 'parked' long after their decisions were implemented."""
    backlog = _backlog()
    decisions = _decisions_by_id()
    checked_any = False
    for item in backlog["items"]:
        if item["status"] != "done":
            continue
        for dec_id in item.get("related_decisions", []):
            decision = decisions.get(dec_id)
            if decision is None:
                continue
            for claim in decision.get("claims", []) or []:
                for check in claim.get("checks", []) or []:
                    if check.get("check_kind") != "grep":
                        continue
                    checked_any = True
                    expect_present = check.get("expect", {}).get("present", True)
                    found = _grep_present(check["path"], check["pattern"])
                    assert found == expect_present, (
                        f"{item['id']} ({dec_id}.{claim['claim_id']}) expects grep "
                        f"pattern {check['pattern']!r} in {check['path']!r} to be "
                        f"{'present' if expect_present else 'absent'}, but the tree "
                        f"disagrees — status 'done' no longer matches the codebase"
                    )
    assert checked_any, "no grep-checkable 'done' item found — this guard would be vacuous"
