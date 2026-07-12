"""Within-job progress reporting (BL-123), usable from any layer.

Lives in ``domain`` so adapters (e.g. the spaCy annotator) can report phase
boundaries without importing ``application``; the hexagonal import direction
(D-013 / BL-052) allows every layer to depend on domain, and nothing here
depends on anything.

The worker's dispatch installs a reporter for the duration of one job; any
code on that call stack may then call :func:`report_progress`. When no
reporter is installed (tests, CLI runs, the API process) reporting is a no-op.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable

#: ``(current, total, stage)``; any element may be ``None`` to leave the
#: previously-reported value in place (the persistence layer merges).
ProgressReporter = Callable[[int | None, int | None, str | None], None]

_PROGRESS_REPORTER: contextvars.ContextVar[ProgressReporter | None] = contextvars.ContextVar(
    "doc_ray_job_progress_reporter", default=None
)


def set_progress_reporter(reporter: ProgressReporter) -> contextvars.Token[ProgressReporter | None]:
    """Install *reporter* for the current context; return the reset token."""
    return _PROGRESS_REPORTER.set(reporter)


def reset_progress_reporter(token: contextvars.Token[ProgressReporter | None]) -> None:
    """Uninstall the reporter previously installed with *token*."""
    _PROGRESS_REPORTER.reset(token)


def report_progress(
    current: int | None = None, total: int | None = None, stage: str | None = None
) -> None:
    """Report progress for the currently running job, when a reporter exists."""
    reporter = _PROGRESS_REPORTER.get()
    if reporter is not None:
        reporter(current, total, stage)
