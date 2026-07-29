from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

SCRIPT = (
    Path(__file__).parents[1]
    / "skills"
    / "doc-ray-graphql-maximizer"
    / "scripts"
    / "check_doc_ray_drift.py"
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_doc_ray_drift", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_documented_root_operations_reads_explicit_inventory(tmp_path: Path) -> None:
    checker = _load_checker()
    playbook = tmp_path / "playbook.md"
    playbook.write_text(
        """\
## Root Operation Inventory

### Query

- `version`
- `corpusStats`

### Mutation

- `reannotate`
- `runOperation`
""",
        encoding="utf-8",
    )

    assert checker.documented_root_operations(playbook) == {
        "Query": {"version", "corpusStats"},
        "Mutation": {"reannotate", "runOperation"},
    }


def test_schema_check_fails_when_live_operations_are_undocumented(
    monkeypatch, tmp_path: Path
) -> None:
    checker = _load_checker()
    references = tmp_path / "references"
    references.mkdir()
    (references / "graphql-value-playbook.md").write_text(
        """\
## Root Operation Inventory

### Query

- `version`

### Mutation

- `reannotate`
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "skill_root", lambda: tmp_path)
    monkeypatch.setattr(
        checker,
        "schema_digest",
        lambda _root: {
            "root_queries": ["version", "corpusStats"],
            "root_mutations": ["reannotate", "runOperation"],
            "focus_types": {"Document": sorted(checker.EXPECTED_DOCUMENT_FIELDS)},
        },
    )
    errors: list[str] = []
    warnings: list[str] = []

    checker.check_schema(tmp_path, errors, warnings)

    assert errors == [
        "playbook missing live Query fields: corpusStats",
        "playbook missing live Mutation fields: runOperation",
    ]
    assert warnings == []


def test_skill_guidance_distinguishes_operation_shapes_and_authority() -> None:
    skill = (SCRIPT.parents[1] / "SKILL.md").read_text(encoding="utf-8")
    playbook = (SCRIPT.parents[1] / "references" / "graphql-value-playbook.md").read_text(
        encoding="utf-8"
    )
    normalized_skill = " ".join(skill.split())
    normalized_playbook = " ".join(playbook.split())

    assert "Page only fields whose live signature exposes pagination arguments" in normalized_skill
    assert "Scheduling and billing mutations require tenant-admin authority" in normalized_skill
    assert "Every mutation either returns a job" not in playbook
    assert "`entityRelations` is direct NPMI co-occurrence evidence" in normalized_playbook
    assert "vision stack is configured" in normalized_playbook
    assert "checkout-generated schema, not deployed-endpoint introspection" in normalized_playbook
