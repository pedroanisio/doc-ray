"""Advertised MCP discovery cost is measured and regression-guarded."""

from __future__ import annotations

import asyncio
import json

from doc_ray.domain import mcp_eval_scoring as scoring
from doc_ray.entrypoints.mcp.server import build_server
from scripts import eval_mcp

_FIELDS = {
    "tool_count",
    "tools_list_chars",
    "tools_list_est_tokens",
    "instructions_chars",
    "resources_count",
    "resource_templates_count",
    "prompts_count",
}


def _report(read_chars: int, write_chars: int) -> dict[str, object]:
    report = scoring.build_report("scripted", [])
    report["surface"] = {
        "read_only": {"tools_list_chars": read_chars},
        "write_enabled": {"tools_list_chars": write_chars},
    }
    return report


def test_surface_regression_exits_one_beyond_tolerance_and_zero_within(tmp_path):
    current = _report(1_200, 2_400)
    low_path = tmp_path / "low.json"
    low_path.write_text(json.dumps(_report(1_000, 2_000)), encoding="utf-8")
    assert eval_mcp._compare_baseline(current, low_path, 0.1, 0, 0.05) == 1

    close_path = tmp_path / "close.json"
    close_path.write_text(json.dumps(_report(1_150, 2_300)), encoding="utf-8")
    assert eval_mcp._compare_baseline(current, close_path, 0.1, 0, 0.05) == 0


def test_surface_report_matches_direct_live_server_measurement():
    surface = asyncio.run(eval_mcp._measure_surface())
    assert set(surface) == {"read_only", "write_enabled"}

    async def direct(enable_write: bool) -> tuple[int, int]:
        server = build_server(enable_write=enable_write)
        tools = await server.list_tools()
        wire = json.dumps([tool.model_dump(exclude_none=True) for tool in tools])
        return len(tools), len(wire)

    for key, enabled in (("read_only", False), ("write_enabled", True)):
        measured = surface[key]
        assert set(measured) == _FIELDS
        assert (measured["tool_count"], measured["tools_list_chars"]) == asyncio.run(
            direct(enabled)
        )
        assert measured["tools_list_est_tokens"] == measured["tools_list_chars"] // 4

    baseline_path = eval_mcp._ROOT / "docs" / "eval" / "mcp-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["surface"] == surface
    rendered = eval_mcp._render_surface(surface)
    assert "advertised MCP surface" in rendered
    assert "read_only" in rendered and "write_enabled" in rendered
    assert "tools_list_chars=" in rendered


def test_cli_accepts_surface_tolerance(monkeypatch):
    seen: dict[str, float] = {}

    def fake_run(args):
        seen["surface_tolerance"] = args.surface_tolerance
        return 0

    monkeypatch.setattr(eval_mcp, "run_scripted_mode", fake_run)
    assert eval_mcp.main(["--surface-tolerance", "0.07"]) == 0
    assert seen == {"surface_tolerance": 0.07}


def test_namespace_experiment_reports_only_what_the_harness_can_measure():
    report = asyncio.run(eval_mcp._measure_namespace_experiment())

    assert report["issue"] == 17
    assert report["hypothesis"] == "family prefixes improve model tool selection"
    assert report["candidate"]["renames"]["search_sentences"] == "corpus_search_sentences"
    assert report["candidate"]["renames"]["entity_profiles"] == "structure_entity_profiles"
    assert report["behavioral_measurement"] == {
        "supported": False,
        "required_mode": "agentic",
        "available_mode": "scripted",
        "reason": "scripted tasks bind tool callables and cannot measure model tool selection",
    }
    assert report["decision"] == "do_not_land"
    assert report["status"] == "inconclusive"
    for mode in ("read_only", "write_enabled"):
        metrics = report["surface"][mode]
        assert metrics["candidate_tool_count"] == metrics["current_tool_count"]
        assert metrics["candidate_tools_list_chars"] > metrics["current_tools_list_chars"]
        assert metrics["delta_chars"] == (
            metrics["candidate_tools_list_chars"] - metrics["current_tools_list_chars"]
        )


def test_namespace_experiment_cli_writes_database_free_report(monkeypatch, tmp_path):
    report = {"issue": 17, "status": "inconclusive", "decision": "do_not_land"}

    async def fake_measure():
        return report

    monkeypatch.setattr(eval_mcp, "_measure_namespace_experiment", fake_measure)
    output = tmp_path / "namespace.json"
    assert eval_mcp.main(["--namespace-experiment", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == report
