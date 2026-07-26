"""Drift guard: the API/webapp internal ports must agree across every file (M4).

The API's in-network port (8000) and the webapp's (5001) are written as literals
in several places — the Compose port mapping, the healthcheck URL, the
``DOCRAY_API_URL`` the webapp/MCP use to reach the API, and the admin-console
nginx upstream defaults. Nothing tied them together, so a port change in one file
could silently diverge from the others (misrouted service). These guards extract
each literal and assert they are identical — not equal to a magic number, but
equal to *each other*, so any single-file drift fails. (Drift-map finding M4.)
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE = _ROOT / "docker-compose.yml"
_MCP_COMPOSE = _ROOT / "docker-compose.mcp.yml"
_ADMIN_COMPOSE = _ROOT / "admin-console" / "docker-compose.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _one(pattern: str, text: str, label: str) -> int:
    """Extract exactly one port from *text*; fail loudly if the anchor moved."""
    matches = re.findall(pattern, text)
    assert matches, f"port anchor not found ({label}) — the file was refactored; update this guard"
    return int(matches[0])


def test_api_internal_port_is_consistent_across_files():
    compose, mcp, admin = _read(_COMPOSE), _read(_MCP_COMPOSE), _read(_ADMIN_COMPOSE)
    ports = {
        "compose api ports mapping": _one(r"\$\{API_PORT:-\d+\}:(\d+)", compose, "api ports"),
        "compose api healthcheck": _one(
            r"http://localhost:(\d+)/readyz", compose, "api healthcheck"
        ),
        "compose DOCRAY_API_URL": _one(
            r"DOCRAY_API_URL:\s*http://api:(\d+)/graphql", compose, "DOCRAY_API_URL"
        ),
        "mcp DOCRAY_API_URL default": _one(r"http://api:(\d+)/graphql", mcp, "mcp DOCRAY_API_URL"),
        "admin nginx API_UPSTREAM default": _one(
            r"API_UPSTREAM:\s*\$\{ADMIN_API_UPSTREAM:-http://api:(\d+)\}", admin, "API_UPSTREAM"
        ),
    }
    assert len(set(ports.values())) == 1, f"API internal port disagrees across files: {ports}"


def test_webapp_internal_port_is_consistent_across_files():
    compose, admin = _read(_COMPOSE), _read(_ADMIN_COMPOSE)
    ports = {
        "compose webapp ports mapping": _one(
            r"\$\{WEBAPP_PORT:-\d+\}:(\d+)", compose, "webapp ports"
        ),
        "compose webapp healthcheck": _one(
            r"http://localhost:(\d+)/'\)", compose, "webapp healthcheck"
        ),
        "admin nginx WEBAPP_UPSTREAM default": _one(
            r"WEBAPP_UPSTREAM:\s*\$\{ADMIN_WEBAPP_UPSTREAM:-http://webapp:(\d+)\}",
            admin,
            "WEBAPP_UPSTREAM",
        ),
    }
    assert len(set(ports.values())) == 1, f"webapp internal port disagrees across files: {ports}"
