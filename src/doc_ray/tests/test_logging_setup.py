"""BL-115 — process-wide logging configuration.

Neither the worker nor the API process configured logging at all before this;
these tests cover the pure, offline configuration logic (no DB/process needed).
"""

from __future__ import annotations

import logging

from doc_ray.logging_setup import configure_logging


def _reset_root() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.WARNING)


def test_configure_logging_defaults_to_info_level():
    _reset_root()
    configure_logging(env={})
    assert logging.getLogger().level == logging.INFO


def test_configure_logging_reads_level_from_env():
    _reset_root()
    configure_logging(env={"DOCRAY_LOG_LEVEL": "DEBUG"})
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_is_idempotent_not_stacking_handlers():
    _reset_root()
    configure_logging(env={})
    configure_logging(env={})
    configure_logging(env={})
    managed = [h for h in logging.getLogger().handlers if getattr(h, "_doc_ray_managed", False)]
    assert len(managed) == 1


def test_configure_logging_applies_custom_format():
    _reset_root()
    configure_logging(env={"DOCRAY_LOG_FORMAT": "CUSTOM %(message)s"})
    handler = next(h for h in logging.getLogger().handlers if getattr(h, "_doc_ray_managed", False))
    assert handler.formatter is not None
    assert handler.formatter._fmt == "CUSTOM %(message)s"
