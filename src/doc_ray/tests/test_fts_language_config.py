"""Static guard for language-aware full-text-search config symmetry."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SQL_CALL_RE = re.compile(
    r"\b(?:to_tsvector|to_tsquery|plainto_tsquery|phraseto_tsquery|websearch_to_tsquery)\s*\(",
    re.IGNORECASE,
)
_SQL_SOURCE_DIRS = ("db/migrations", "src/doc_ray", "webapp", "scripts", "tests")
_MIGRATION_PREFIX_RE = re.compile(r"^(\d{3})_")


def _sqlish_statements(text: str) -> list[str]:
    """Return semicolon-delimited chunks likely to contain SQL calls."""
    return [chunk for chunk in text.split(";") if _SQL_CALL_RE.search(chunk)]


def test_fts_sql_calls_route_language_config_through_docray_ts_config():
    """Every FTS vector/query call must share the database language resolver.

    Migration 016 made writes language-aware through ``docray_ts_config(lang)``;
    read paths must keep using the same resolver so future English-only
    hardcoding cannot silently break Portuguese or language-null documents.
    """
    offenders: list[str] = []
    for rel_dir in _SQL_SOURCE_DIRS:
        for path in (_ROOT / rel_dir).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".sql"}:
                continue
            if path.parent.name == "migrations":
                prefix = _MIGRATION_PREFIX_RE.match(path.name)
                if prefix is not None and int(prefix.group(1)) < 16:
                    continue
            rel = path.relative_to(_ROOT)
            text = path.read_text(encoding="utf-8", errors="ignore")
            for statement in _sqlish_statements(text):
                if "docray_ts_config" not in statement:
                    first_line = text[: text.index(statement) + len(statement)].count("\n") + 1
                    offenders.append(f"{rel}:{first_line}")
    assert offenders == []
