"""BL-113 supply-chain automation guards for CI and dependency updates."""

from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_CI = _ROOT / ".github" / "workflows" / "ci.yml"
_DEPENDABOT = _ROOT / ".github" / "dependabot.yml"


def _workflow() -> dict[str, object]:
    return yaml.safe_load(_CI.read_text(encoding="utf-8"))


def _run_steps(job_name: str) -> list[str]:
    workflow = _workflow()
    jobs = workflow["jobs"]
    steps = jobs[job_name]["steps"]
    return [str(step.get("run", "")) for step in steps if isinstance(step, dict)]


def test_ci_runs_python_dependency_vulnerability_scan() -> None:
    runs = "\n".join(_run_steps("static"))

    assert "pip-audit" in runs
    assert "--requirement requirements.txt" in runs
    assert "--requirement requirements-dev.txt" in runs
    assert "--strict" in runs


def test_ci_generates_and_uploads_production_image_sbom() -> None:
    workflow_text = _CI.read_text(encoding="utf-8")
    runs = "\n".join(_run_steps("static"))

    assert "docker build" in runs
    assert "doc-ray:${{ github.sha }}" in runs
    assert "anchore/sbom-action" in workflow_text
    assert "actions/upload-artifact" in workflow_text
    assert "doc-ray-image-sbom" in workflow_text


def test_dependabot_covers_python_docker_and_actions() -> None:
    config = yaml.safe_load(_DEPENDABOT.read_text(encoding="utf-8"))
    updates = config["updates"]
    ecosystems = {entry["package-ecosystem"] for entry in updates}
    roots = {
        (entry["package-ecosystem"], entry["directory"])
        for entry in updates
        if "directory" in entry
    }

    assert {"pip", "docker", "github-actions"} <= ecosystems
    assert ("pip", "/") in roots
    assert ("docker", "/") in roots
    assert ("github-actions", "/") in roots
