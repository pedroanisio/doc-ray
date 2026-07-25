"""Guard: the worker-only ML optional-dependency extras stay declared.

The semantic-embedding job (NLP Layer 20) needs sentence-transformers on the
worker. It is deliberately absent from the base image, so it must be installable
via the `.[semantic]` extra — this test fails if that extra is dropped, which
would silently make the cutover non-runnable.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

_PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def test_semantic_extra_declares_sentence_transformers() -> None:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"].get("optional-dependencies", {})
    assert "semantic" in extras, "the `.[semantic]` worker extra must be declared"
    assert any("sentence-transformers" in dep for dep in extras["semantic"]), extras["semantic"]
