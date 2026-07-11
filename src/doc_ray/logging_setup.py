"""Process-wide structured logging configuration (BL-115).

Neither the worker nor the API process configured logging at all — output was
``print()`` or, for the handful of loggers that already existed implicitly
(e.g. Strawberry's own ``strawberry.execution`` logger), whatever Python's
"handler of last resort" does by default: unformatted, uncontrollable via
environment, easy to mistake for "nothing is logged at all". Call
:func:`configure_logging` once per process entrypoint (``entrypoints.worker``,
``entrypoints.api.app``) — never per-request/per-job — to install one
formatted handler on the root logger, level/format controlled by
``DOCRAY_LOG_LEVEL``/``DOCRAY_LOG_FORMAT``. Every ``logging.getLogger(...)``
call anywhere in the codebase (including Strawberry's own) then propagates
through it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

#: Marks a handler this module installed, so a second call replaces it
#: instead of stacking a duplicate (configure_logging is safe to call more
#: than once — tests, or a process that re-initializes).
_MANAGED_ATTR = "_doc_ray_managed"


def configure_logging(env: Mapping[str, str] | None = None) -> None:
    """Configure the root logger's level and format from the environment.

    Args:
        env: Environment mapping to read from; defaults to ``os.environ``.
    """
    import os

    source = os.environ if env is None else env
    level_name = source.get("DOCRAY_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    if not isinstance(level, int):
        level = logging.INFO
    fmt = source.get("DOCRAY_LOG_FORMAT", "").strip() or _DEFAULT_FORMAT

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, _MANAGED_ATTR, False):
            root.removeHandler(handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter(fmt))
    setattr(stream_handler, _MANAGED_ATTR, True)
    root.addHandler(stream_handler)
