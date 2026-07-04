"""Configuration for a db_analyzer run.

Settings are resolved in increasing precedence: built-in defaults, then a JSON
config file (if any), then ``DB_ANALYZER_*`` environment variables, then
explicit CLI overrides passed to :func:`load_config`. Nothing here touches a
database — it is a pure assembly step, so it is unit-testable without one.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_SAMPLE_THRESHOLD = 100_000
DEFAULT_DUPLICATE_THRESHOLD = 200_000
DEFAULT_OUTPUT_DIR = "./db_analysis_out"


@dataclass(frozen=True)
class AnalyzerConfig:
    """Resolved settings for one analysis run.

    Attributes:
        engine: Registered engine name (e.g. ``"postgres"``).
        dsn: Connection string, in the engine's native format.
        include_schemas: Schemas to inspect; empty means "all non-system schemas".
        exclude_schemas: Schemas to skip even if ``include_schemas`` is empty.
        sample_threshold: Row-count above which ``distinct_count`` falls back
            to the engine's planner statistics instead of an exact scan.
        duplicate_threshold: Row-count above which the whole-row duplicate
            scan is skipped rather than run.
        output_dir: Directory the Markdown/JSON reports are written to.
        read_only: Whether the connection is opened in a database-enforced
            read-only transaction mode. Only set to ``False`` deliberately.
        structure_only: Skip data profiling and report structure alone.
    """

    engine: str = "postgres"
    dsn: str = "postgresql://docray:docray@localhost:5432/docray"
    include_schemas: tuple[str, ...] = ()
    exclude_schemas: tuple[str, ...] = ()
    sample_threshold: int = DEFAULT_SAMPLE_THRESHOLD
    duplicate_threshold: int = DEFAULT_DUPLICATE_THRESHOLD
    output_dir: str = DEFAULT_OUTPUT_DIR
    read_only: bool = True
    structure_only: bool = False


def _csv(value: str) -> tuple[str, ...]:
    """Split a comma-separated env/config value into a trimmed, non-empty tuple.

    Args:
        value: Raw comma-separated string.

    Returns:
        Trimmed, non-empty parts, in order.
    """
    return tuple(p.strip() for p in value.split(",") if p.strip())


def _load_file(path: str) -> dict[str, Any]:
    """Read a JSON config file.

    Args:
        path: Path to a JSON file.

    Returns:
        Its parsed top-level object.

    Raises:
        ValueError: If the file's top level is not a JSON object.
    """
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"config file {path} must contain a JSON object at the top level")
    return data


def _apply_file(cfg: AnalyzerConfig, data: dict[str, Any]) -> AnalyzerConfig:
    """Overlay JSON config-file keys onto a config.

    Args:
        cfg: Base config.
        data: Parsed JSON config-file contents; unknown keys are ignored.

    Returns:
        A new config with recognized keys applied.
    """
    updates: dict[str, Any] = {}
    for key in (
        "engine",
        "dsn",
        "sample_threshold",
        "duplicate_threshold",
        "output_dir",
        "read_only",
        "structure_only",
    ):
        if key in data:
            updates[key] = data[key]
    if "include_schemas" in data:
        updates["include_schemas"] = tuple(data["include_schemas"])
    if "exclude_schemas" in data:
        updates["exclude_schemas"] = tuple(data["exclude_schemas"])
    return replace(cfg, **updates)


def _apply_env(cfg: AnalyzerConfig, env: Mapping[str, str]) -> AnalyzerConfig:
    """Overlay ``DB_ANALYZER_*`` (and ``DATABASE_URL``) environment variables onto a config.

    Args:
        cfg: Base config.
        env: Environment mapping (typically ``os.environ``).

    Returns:
        A new config with recognized environment variables applied.
    """
    updates: dict[str, Any] = {}
    if "DATABASE_URL" in env:
        updates["dsn"] = env["DATABASE_URL"]
    if "DB_ANALYZER_DSN" in env:
        updates["dsn"] = env["DB_ANALYZER_DSN"]
    if "DB_ANALYZER_ENGINE" in env:
        updates["engine"] = env["DB_ANALYZER_ENGINE"]
    if "DB_ANALYZER_SCHEMAS" in env:
        updates["include_schemas"] = _csv(env["DB_ANALYZER_SCHEMAS"])
    if "DB_ANALYZER_EXCLUDE_SCHEMAS" in env:
        updates["exclude_schemas"] = _csv(env["DB_ANALYZER_EXCLUDE_SCHEMAS"])
    if "DB_ANALYZER_SAMPLE_THRESHOLD" in env:
        updates["sample_threshold"] = int(env["DB_ANALYZER_SAMPLE_THRESHOLD"])
    if "DB_ANALYZER_DUPLICATE_THRESHOLD" in env:
        updates["duplicate_threshold"] = int(env["DB_ANALYZER_DUPLICATE_THRESHOLD"])
    if "DB_ANALYZER_OUTPUT_DIR" in env:
        updates["output_dir"] = env["DB_ANALYZER_OUTPUT_DIR"]
    if "DB_ANALYZER_READ_ONLY" in env:
        updates["read_only"] = env["DB_ANALYZER_READ_ONLY"].strip().lower() not in (
            "0",
            "false",
            "no",
        )
    if "DB_ANALYZER_STRUCTURE_ONLY" in env:
        updates["structure_only"] = env["DB_ANALYZER_STRUCTURE_ONLY"].strip().lower() in (
            "1",
            "true",
            "yes",
        )
    return replace(cfg, **updates)


def load_config(
    *,
    env: Mapping[str, str] | None = None,
    config_path: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> AnalyzerConfig:
    """Resolve an :class:`AnalyzerConfig` from defaults, a config file, env vars, and overrides.

    Precedence, lowest to highest: dataclass defaults, ``config_path`` file
    contents, ``env`` variables, ``overrides`` (typically parsed CLI args).

    Args:
        env: Environment mapping; defaults to ``os.environ``.
        config_path: Path to a JSON config file, or ``None`` to skip it. Falls
            back to the ``DB_ANALYZER_CONFIG`` environment variable.
        overrides: Explicit field values that win over everything else
            (``None`` values are ignored, so callers can pass a full argparse
            namespace's ``__dict__`` without unsetting unrelated fields).

    Returns:
        The fully resolved config.
    """
    resolved_env: Mapping[str, str] = os.environ if env is None else env
    cfg = AnalyzerConfig()

    path = config_path or resolved_env.get("DB_ANALYZER_CONFIG")
    if path:
        cfg = _apply_file(cfg, _load_file(path))

    cfg = _apply_env(cfg, resolved_env)

    if overrides:
        fields = AnalyzerConfig.__dataclass_fields__
        real = {k: v for k, v in overrides.items() if v is not None and k in fields}
        if "include_schemas" in real:
            real["include_schemas"] = tuple(real["include_schemas"])
        if "exclude_schemas" in real:
            real["exclude_schemas"] = tuple(real["exclude_schemas"])
        cfg = replace(cfg, **real)

    return cfg
