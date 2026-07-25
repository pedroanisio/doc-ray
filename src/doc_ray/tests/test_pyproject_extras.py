"""Guard: the learned sentence encoder is baked into the base image.

NLP Layer 20's ``semantic_embed`` job needs ``sentence-transformers`` on the
worker. The heavy tensor stack it rides on (``torch`` + ``transformers``) is
already a base dependency — it is pulled in by ``spacy[transformers]`` and by the
Prism visual encoder (``open_clip_torch``). So excluding the sentence encoder from
the base image saves nothing meaningful while making the cutover non-runnable:
``uv sync --locked`` (the production ``Dockerfile``) never installs an optional
extra, so a worker built without it fails every embed job with
``SemanticStackUnavailableError``.

The fix mirrors the Prism precedent: ``sentence-transformers`` is a **base**
dependency (single-sourced from ``requirements.txt`` into
``[project.dependencies]``), exactly like ``open_clip_torch``. The ``.[semantic]``
extra is kept only as a convenience alias for callers that install by extra name.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _ROOT / "pyproject.toml"
_REQUIREMENTS = _ROOT / "requirements.txt"


def _base_requirements() -> list[str]:
    """Return non-comment, non-blank lines from the base requirements file."""
    lines = _REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def test_sentence_transformers_is_a_base_dependency() -> None:
    """The encoder must be baked into the image, not hidden behind an extra.

    Mirrors ``open_clip_torch``: both ride the base ``torch`` stack, so both are
    base pins. A worker built from ``requirements.txt`` must carry the encoder.
    """
    base = _base_requirements()
    assert any("sentence-transformers" in req for req in base), (
        "sentence-transformers must be a BASE dependency in requirements.txt so the "
        "production image (uv sync --locked) actually installs it — see how "
        "open_clip_torch is pinned. Base pins found: " + repr(base)
    )
    # Sanity anchor: the Prism precedent this mirrors is genuinely a base pin.
    assert any("open_clip_torch" in req for req in base), repr(base)


def test_semantic_extra_remains_as_a_convenience_alias() -> None:
    """The ``.[semantic]`` extra stays declared for install-by-extra-name callers."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"].get("optional-dependencies", {})
    assert "semantic" in extras, "the `.[semantic]` alias extra must remain declared"
    assert any("sentence-transformers" in dep for dep in extras["semantic"]), extras["semantic"]
