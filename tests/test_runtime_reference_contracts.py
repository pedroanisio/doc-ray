"""Drift guards for runtime references embedded in templates and deploy files."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _template_url_targets() -> tuple[set[str], int]:
    targets: set[str] = set()
    template_count = 0
    for path in sorted((ROOT / "webapp" / "templates").rglob("*.html")):
        template_count += 1
        text = path.read_text(encoding="utf-8")
        targets.update(re.findall(r"\burl_for\(\s*['\"]([^'\"]+)['\"]", text))
    return targets, template_count


def _deployment_module_references() -> set[str]:
    paths = [ROOT / "Dockerfile", ROOT / "Makefile", *sorted(ROOT.glob("docker-compose*.yml"))]
    modules: set[str] = set()
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            modules.update(re.findall(r"\bdoc_ray\.entrypoints\.[A-Za-z_][A-Za-z0-9_.]*", line))
    return modules


def _missing_modules(modules: set[str]) -> list[str]:
    return sorted(module for module in modules if importlib.util.find_spec(module) is None)


def test_every_template_url_target_is_a_registered_flask_endpoint() -> None:
    from webapp.app import app

    targets, template_count = _template_url_targets()
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}

    assert targets <= endpoints, f"unknown template url_for targets: {sorted(targets - endpoints)}"
    assert template_count >= 27, "template inventory shrank without updating this guard"
    assert len(targets) >= 50, "url_for target inventory shrank without updating this guard"


def test_every_deployment_entrypoint_module_resolves() -> None:
    modules = _deployment_module_references()

    assert not _missing_modules(modules)
    assert len(modules) >= 8, "deployment module inventory shrank without updating this guard"


def test_missing_module_guard_fails_closed() -> None:
    assert _missing_modules({"doc_ray.entrypoints.this_module_does_not_exist"}) == [
        "doc_ray.entrypoints.this_module_does_not_exist"
    ]
