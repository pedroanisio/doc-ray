"""GraphQL root fields must have a human binding or a reasoned API-only ledger entry."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from scripts import check_surface_parity as guard


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    api = tmp_path / "api"
    web = tmp_path / "webapp"
    api.mkdir()
    web.mkdir()
    (api / "query.py").write_text(
        """
import strawberry

class FakeQueries:
    @strawberry.field
    def field_x(self) -> str:
        return "x"

    @strawberry.field
    def field_y(self) -> str:
        return "y"

class Mutation:
    @strawberry.mutation
    def field_z(self) -> bool:
        return True

class NestedType:
    @strawberry.field
    def nested_only(self) -> str:
        return "not a root field"
""",
        encoding="utf-8",
    )
    (web / "view.py").write_text(
        'QUERY = "query { fieldX }"\n',
        encoding="utf-8",
    )
    return api, web


def test_snake_to_camel_handles_multiple_underscores():
    assert guard.snake_to_camel("decide_visual_captions") == "decideVisualCaptions"


def test_analyze_classifies_root_fields_without_counting_nested_types(tmp_path):
    api, web = _fixture(tmp_path)

    report = guard.analyze(api, web, allowlist={})

    assert [(item.name, item.status) for item in report.fields] == [
        ("fieldX", "surfaced"),
        ("fieldY", "unsurfaced"),
        ("fieldZ", "unsurfaced"),
    ]
    assert "nestedOnly" not in {item.name for item in report.fields}
    assert report.violations == (
        "UNSURFACED fieldY",
        "UNSURFACED fieldZ",
    )


def test_reasoned_allowlist_suppresses_gap_but_stale_and_empty_entries_fail(tmp_path):
    api, web = _fixture(tmp_path)

    report = guard.analyze(
        api,
        web,
        allowlist={
            "fieldY": "Rendered through a bounded direct-SQL view.",
            "fieldZ": "",
            "fieldW": "Old field that no longer exists.",
        },
    )

    assert next(item for item in report.fields if item.name == "fieldY").status == "allowlisted"
    assert "EMPTY-REASON fieldZ" in report.violations
    assert "STALE-ALLOWLIST fieldW" in report.violations


def test_empty_reason_fails_even_when_the_field_is_already_surfaced(tmp_path):
    api, web = _fixture(tmp_path)

    report = guard.analyze(api, web, allowlist={"fieldX": ""})

    assert "EMPTY-REASON fieldX" in report.violations


def test_parse_failure_is_reported_not_silently_skipped(tmp_path):
    api, web = _fixture(tmp_path)
    (api / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    report = guard.analyze(api, web, allowlist={})

    assert any(item.startswith("PARSE-ERROR ") for item in report.violations)


def test_cli_report_and_strict_exit_codes(tmp_path, capsys):
    api, web = _fixture(tmp_path)
    args = ["--api-root", str(api), "--webapp-root", str(web)]

    assert guard.main(args, allowlist={}) == 0
    report_output = capsys.readouterr().out
    assert "SURFACE-PARITY REPORT:" in report_output
    assert "UNSURFACED fieldY" in report_output

    assert guard.main([*args, "--strict"], allowlist={}) == 1
    strict_output = capsys.readouterr().out
    assert "SURFACE-PARITY VIOLATION:" in strict_output


def test_cli_strict_is_clean_when_every_field_is_bound_or_reasoned(tmp_path, capsys):
    api, web = _fixture(tmp_path)
    args = ["--api-root", str(api), "--webapp-root", str(web), "--strict"]

    result = guard.main(
        args,
        allowlist={"fieldY": "Direct-SQL page.", "fieldZ": "Operator-only mutation."},
    )

    assert result == 0
    assert "SURFACE-PARITY OK: 3 GraphQL root fields classified" in capsys.readouterr().out


def test_real_tree_report_is_sorted_and_strict_is_clean(capsys):
    started = perf_counter()
    report = guard.analyze(guard.DEFAULT_API_ROOT, guard.DEFAULT_WEBAPP_ROOT)

    assert report.fields
    assert perf_counter() - started < 1.0
    assert [item.name for item in report.fields] == sorted(item.name for item in report.fields)
    assert guard.main(["--strict"]) == 0
    assert "SURFACE-PARITY OK:" in capsys.readouterr().out


def test_d034_promotes_the_strict_checker_to_both_landing_surfaces():
    root = Path(__file__).resolve().parents[1]
    decisions = (root / "engineering_decisions.yml").read_text(encoding="utf-8")
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    instructions = (root / "CLAUDE.md").read_text(encoding="utf-8")
    command = "scripts/check_surface_parity.py --strict"

    assert 'id:                 "D-034"' in decisions
    assert f'command:     "python {command}"' in decisions
    assert makefile.count(command) == 2
    assert f"python {command}" in instructions
