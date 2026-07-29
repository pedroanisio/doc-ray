"""The format allowlist has one definition, and every surface reads it.

The extension list was re-typed by hand across the upload form, the tutorial,
the public docs and the project overview, and every copy drifted behind the
extractor registry: HTML, SVG and PPTX have shipped extractors that none of
those surfaces mention, and the upload form's ``accept`` attribute silently
excluded them. A hand-copied list cannot be kept honest, so the list moves to
``domain.document_formats`` and the registry is asserted against it here.
"""

from __future__ import annotations

from doc_ray.adapters import extraction
from doc_ray.domain import document_formats as formats


def test_the_registry_and_the_allowlist_are_the_same_set() -> None:
    """A dispatchable extension that the allowlist omits is invisible to callers."""
    assert set(extraction.supported_extensions()) == set(formats.SUPPORTED_EXTENSIONS)


def test_every_supported_extension_has_a_content_type() -> None:
    """A stored blob with no Content-Type cannot be served back correctly."""
    assert set(formats.CONTENT_TYPES) == set(formats.SUPPORTED_EXTENSIONS)


def test_the_accept_attribute_covers_every_supported_extension() -> None:
    """The browser hint must not be narrower than what the server accepts."""
    accepted = {part.strip() for part in formats.ACCEPT_ATTRIBUTE.split(",")}

    assert accepted == set(formats.SUPPORTED_EXTENSIONS)


def test_extensions_are_lowercase_and_dotted() -> None:
    """``is_supported_extension`` lowercases its input; the allowlist must match."""
    for extension in formats.SUPPORTED_EXTENSIONS:
        assert extension.startswith(".")
        assert extension == extension.lower()


def test_the_prose_label_names_every_distinct_format() -> None:
    """The human-facing list is derived, so it cannot drift behind the registry."""
    label = formats.format_label()

    for name in ("PDF", "EPUB", "HTML", "Markdown", "DOCX", "PPTX", "SVG"):
        assert name in label


def test_supported_format_names_are_a_public_registry_surface() -> None:
    """Documentation guards must derive format names instead of copying them."""
    assert formats.SUPPORTED_FORMAT_NAMES == (
        "PDF",
        "EPUB",
        "HTML",
        "Markdown",
        "plain text",
        "DOCX",
        "PPTX",
        "SVG",
    )
