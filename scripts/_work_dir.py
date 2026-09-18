"""Resolve where local tooling writes scratch output and reports.

Scripts that produce screenshots, benchmark output, evaluation reports or
telemetry snapshots write operating data, not source. That data belongs in the
local working store named by ``DOCRAY_WORK_DIR`` (a directory outside the
repository), never in the tree. When the variable is unset, the single
gitignored ``_work/`` directory at the repository root is used so a fresh
checkout still works; nothing under it may be committed (the tracked-ignored
gate refuses it).
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

ENV_VAR = "DOCRAY_WORK_DIR"


def work_dir(*parts: str, create: bool = True) -> Path:
    """Return ``$DOCRAY_WORK_DIR/<parts>`` (or ``<repo>/_work/<parts>`` when unset)."""
    base = os.environ.get("DOCRAY_WORK_DIR", "").strip()
    root = Path(base).expanduser() if base else _REPO / "_work"
    target = root.joinpath(*parts)
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target
