"""Regression guards for environment-variable documentation boundaries."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _admin_console_env_reads() -> set[str]:
    found: set[str] = set()
    for path in (_ROOT / "admin-console" / "src").rglob("*.ts"):
        text = path.read_text(encoding="utf-8")
        found.update(re.findall(r"import\.meta\.env\??\.([A-Z][A-Z0-9_]+)", text))
    return found


def _documented_env_vars(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.MULTILINE))


def test_admin_console_env_example_documents_runtime_env_reads():
    """admin-console/.env.example must document every Vite env key the console reads."""
    missing = sorted(
        _admin_console_env_reads()
        - _documented_env_vars(_ROOT / "admin-console" / ".env.example")
    )

    assert not missing
