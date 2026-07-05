"""BL-052 — enforce the hexagonal import direction by test.

The dependency rule (``docs/architecture.md``): dependencies point inward only.
The two innermost layers must never import outward —

* ``doc_ray.domain`` imports nothing from any other ``doc_ray`` layer (it is
  dependency-free; only ``doc_ray.domain`` internals are allowed);
* ``doc_ray.ports`` import only ``doc_ray.domain`` — never ``adapters``,
  ``application`` or ``entrypoints``.

This walks the real package with the AST and fails on any violation; a self-test
proves the checker actually detects one (so the guard cannot pass vacuously).
"""

from __future__ import annotations

import ast
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1]  # src/doc_ray
assert _PKG.name == "doc_ray"

#: layer -> the doc_ray sublayers it may NOT import (points-inward-only).
_FORBIDDEN = {
    "domain": ("ports", "adapters", "application", "entrypoints", "config"),
    "ports": ("adapters", "application", "entrypoints"),
}


def _doc_ray_sublayers(source: str) -> set[str]:
    """Return the ``doc_ray.<sublayer>`` names imported by *source* (AST-parsed)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        for name in names:
            parts = name.split(".")
            if len(parts) >= 2 and parts[0] == "doc_ray":
                found.add(parts[1])
    return found


def _violations() -> list[str]:
    bad: list[str] = []
    for layer, forbidden in _FORBIDDEN.items():
        for py in sorted((_PKG / layer).rglob("*.py")):
            for sub in sorted(_doc_ray_sublayers(py.read_text(encoding="utf-8")) & set(forbidden)):
                bad.append(
                    f"{py.relative_to(_PKG.parent)} imports doc_ray.{sub} "
                    f"(forbidden from the '{layer}' layer)"
                )
    return bad


def test_domain_and_ports_do_not_import_outward():
    bad = _violations()
    assert not bad, "hexagonal import-direction violated:\n  " + "\n  ".join(bad)


def test_checker_detects_a_violation():
    """The checker must flag a forbidden import — otherwise the guard is vacuous."""
    offending = "from doc_ray.adapters.persistence import Repo\nimport doc_ray.application.jobs\n"
    subs = _doc_ray_sublayers(offending)
    assert "adapters" in subs and "application" in subs
    assert subs & set(_FORBIDDEN["domain"])  # would be flagged inside domain/
    # a domain-internal import is allowed — not one of the forbidden sublayers
    clean = _doc_ray_sublayers("from doc_ray.domain.ulid import ULIDFactory\n")
    assert clean == {"domain"} and not (clean & set(_FORBIDDEN["domain"]))
