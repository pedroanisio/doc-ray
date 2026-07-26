"""Drift guard: every ``D-NNN`` ADR citation must resolve to a real decision (M2).

``engineering_decisions.yml`` is the validated ADR system of record, but the
``D-0xx`` references scattered through code comments, docstrings, ``CLAUDE.md``,
``README.md``, and ``docs/`` are hand-typed and were unchecked — an adversarial
audit found real wrong IDs (``D-024`` where ``D-025`` was meant; a wrong migration
number on ``D-007``). This guard extracts every cited ``D-NNN`` token and asserts
it names a decision that exists in the registry, so a typo'd or dangling ADR
citation is a test failure instead of a silent lie. (Drift-map finding M2.)
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY = _ROOT / "engineering_decisions.yml"
_CITATION_RE = re.compile(r"\bD-\d{3}\b")

#: Directories scanned for citations (code comments/docstrings + prose).
_SCAN_DIRS = ("src/doc_ray", "webapp", "scripts", "docs")
#: Root-level docs that cite ADRs.
_SCAN_ROOT_FILES = ("CLAUDE.md", "README.md", "AGENTS.md")
_SCAN_SUFFIXES = {".py", ".md"}

#: Decisions cited in code as a precedent that are DELIBERATELY not (yet) in the
#: validated registry. ``D-018`` (frontier/visual-caption cost is always an
#: explicit request, never auto-chained) is an in-code decision marker documented
#: as such in docs/proposals/prism-visual-layer.md and src/doc_ray/domain/jobs.py;
#: the proposal recommends promoting it to the registry. Until then it is a known,
#: intentional exception — not the typo class this guard targets.
_KNOWN_UNREGISTERED = frozenset({"D-018"})


def _registered_ids() -> set[str]:
    text = _REGISTRY.read_text(encoding="utf-8")
    return set(re.findall(r'id:\s*"?(D-\d{3})"?', text))


def _cited_ids() -> dict[str, list[str]]:
    """Map each cited ``D-NNN`` to the repo-relative files that cite it."""
    cites: dict[str, set[str]] = {}
    paths: list[Path] = [_ROOT / name for name in _SCAN_ROOT_FILES]
    for rel in _SCAN_DIRS:
        base = _ROOT / rel
        if base.exists():
            paths.extend(p for p in base.rglob("*") if p.suffix in _SCAN_SUFFIXES)
    for path in paths:
        if not path.is_file():
            continue
        for token in set(_CITATION_RE.findall(path.read_text(encoding="utf-8"))):
            cites.setdefault(token, set()).add(str(path.relative_to(_ROOT)))
    return {token: sorted(files) for token, files in cites.items()}


def test_every_adr_citation_resolves_to_a_registered_decision():
    registered = _registered_ids()
    allowed = registered | _KNOWN_UNREGISTERED
    cited = _cited_ids()

    dangling = {token: files for token, files in cited.items() if token not in allowed}
    assert not dangling, (
        "ADR citation(s) name no decision in engineering_decisions.yml "
        f"(and are not the documented D-018 exception): {dangling}. "
        "Fix the citation or add the decision to the registry."
    )


def test_known_unregistered_markers_are_actually_absent_from_the_registry():
    """The D-018 exception must be self-correcting: drop it once it is promoted.

    If someone adds D-018 to engineering_decisions.yml, this fails — a nudge to
    remove the now-redundant allowlist entry so the guard has no dead exceptions.
    """
    registered = _registered_ids()
    stale = sorted(_KNOWN_UNREGISTERED & registered)
    assert not stale, (
        f"these markers are now IN the registry — drop them from _KNOWN_UNREGISTERED: {stale}"
    )


def test_citation_scan_is_nonvacuous():
    """Guard the guard: the registry and the scan must both find real content."""
    registered = _registered_ids()
    assert len(registered) >= 30, f"only {len(registered)} decisions parsed — registry regex broke?"
    cited = _cited_ids()
    # D-015 (the net-LOC cap) is cited pervasively; its absence means the scan broke.
    assert "D-015" in cited, "citation scan found no D-015 — the scanner or paths regressed"
