#!/usr/bin/env python3
"""Fail if a form control is named by its placeholder, or by nothing at all.

The ``placeholder`` attribute is a *hint*. It renders in low-contrast text that
is hard to read, it vanishes the instant the user starts typing, and when it
merely repeats the field name it is redundant. Pressed into service as the
label, it leaves the user with a form whose fields become anonymous exactly when
they are being filled in — and it leaves anyone reviewing their answers with no
way to tell what a value was supposed to be.

So: give every field a real label, and let the placeholder do what it is for —
showing an example of an acceptable value.

A control satisfies this gate when it carries any of:

* a wrapping ``<label>`` (implicit association),
* a ``<label for="…">`` matching its ``id``, in either document order,
* an explicit ``aria-label`` / ``aria-labelledby``.

Controls the user does not fill in — hidden fields, submit/reset/image buttons,
and ``<button>`` (which is named by its own content) — are out of scope.

Templates are Jinja, not HTML, so control-flow tags are stripped and expressions
are collapsed to a placeholder token before parsing. That is enough to recover
the element tree, which is all this gate reads.

Usage::

    python scripts/check_form_labels.py
    python scripts/check_form_labels.py path/to/templates

Exit codes: 0 = every control is labelled; 1 = one or more are not.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

#: Template trees scanned by default.
DEFAULT_ROOTS: tuple[Path, ...] = (_ROOT / "webapp" / "templates",)

#: Elements that accept user input and therefore need a name.
_CONTROL_TAGS: frozenset[str] = frozenset({"input", "select", "textarea"})

#: ``input`` types that are not filled in by the user, so they are out of scope.
#: ``button``/``submit``/``reset`` are named by their own ``value``; ``hidden``
#: is never presented; ``image`` is named by ``alt``.
_EXEMPT_INPUT_TYPES: frozenset[str] = frozenset({"hidden", "submit", "reset", "button", "image"})

_JINJA_BLOCK = re.compile(r"\{%.*?%\}", re.DOTALL)
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_JINJA_EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)


@dataclass(frozen=True)
class Violation:
    """One control whose accessible name is missing or is only a placeholder."""

    path: str
    line: int
    tag: str
    name: str
    reason: str

    def __str__(self) -> str:
        """Render the finding as one reviewable line."""
        return f"{self.path}:{self.line}: <{self.tag} name={self.name!r}> {self.reason}"


class _FormScanner(HTMLParser):
    """Collect controls and the labels that could name them."""

    def __init__(self) -> None:
        """Start with no open labels and nothing collected."""
        super().__init__(convert_charrefs=True)
        self.label_depth = 0
        self.labelled_ids: set[str] = set()
        self.controls: list[tuple[str, dict[str, str], int, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record a label association or a control needing one."""
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "label":
            self.label_depth += 1
            if values.get("for"):
                self.labelled_ids.add(values["for"])
            return
        if tag in _CONTROL_TAGS:
            line = self.getpos()[0]
            self.controls.append((tag, values, line, self.label_depth > 0))

    def handle_endtag(self, tag: str) -> None:
        """Close an open label scope."""
        if tag == "label" and self.label_depth:
            self.label_depth -= 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Treat a self-closed control the same as an open one."""
        self.handle_starttag(tag, attrs)


def _neutralize(text: str) -> str:
    """Return template source with Jinja syntax reduced to inert text.

    Control-flow and comment tags are removed outright; expressions become a
    literal token so an attribute like ``value="{{ doc.title }}"`` keeps its
    quoting and the element tree still parses.
    """
    text = _JINJA_COMMENT.sub("", text)
    text = _JINJA_BLOCK.sub("", text)
    return _JINJA_EXPR.sub("x", text)


def _scan(path: Path) -> list[Violation]:
    """Return every unlabelled or placeholder-named control in one template."""
    scanner = _FormScanner()
    scanner.feed(_neutralize(path.read_text(encoding="utf-8")))
    scanner.close()

    findings: list[Violation] = []
    for tag, values, line, wrapped in scanner.controls:
        if tag == "input" and values.get("type", "text").lower() in _EXEMPT_INPUT_TYPES:
            continue
        named = (
            wrapped
            or bool(values.get("aria-label"))
            or bool(values.get("aria-labelledby"))
            or (bool(values.get("id")) and values["id"] in scanner.labelled_ids)
        )
        if named:
            continue
        reason = "placeholder-as-label" if values.get("placeholder") else "unlabelled"
        findings.append(
            Violation(
                path=str(path),
                line=line,
                tag=tag,
                name=values.get("name", ""),
                reason=reason,
            )
        )
    return findings


def find_violations(roots: tuple[Path, ...] | tuple[str, ...] | None = None) -> list[Violation]:
    """Return every control that is unlabelled or named only by a placeholder.

    Args:
        roots: Directories (or files) to scan; defaults to the browser templates.

    Returns:
        Findings ordered by path then line.
    """
    findings: list[Violation] = []
    for root in roots if roots is not None else DEFAULT_ROOTS:
        base = Path(root)
        targets = sorted(base.rglob("*.html")) if base.is_dir() else [base]
        for path in targets:
            findings.extend(_scan(path))
    return sorted(findings, key=lambda v: (v.path, v.line))


def main(argv: list[str] | None = None) -> int:
    """Run the gate and report findings.

    Returns:
        0 when every control carries a real name, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="check_form_labels",
        description="Fail if a form control is named by its placeholder, or by nothing.",
    )
    parser.add_argument("roots", nargs="*", help="template directories to scan")
    args = parser.parse_args(argv)

    roots = tuple(args.roots) if args.roots else None
    violations = find_violations(roots)
    for violation in violations:
        print(f"  {violation}")
    if violations:
        print(f"form-label: {len(violations)} control(s) without a real label")
        return 1
    print("form-label: every control carries a real label")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
