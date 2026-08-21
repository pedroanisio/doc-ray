#!/usr/bin/env python3
"""Fail if a control misses the platform touch-target minimum on touch devices.

A *touch target* is the area a user can tap to perform an action. It is not the
same object as the control's visual representation: an active area larger than
the drawn control is the normal way to reconcile limited screen space with
fingers that need comfortable targets. Sizing a target to the ink instead of to
the finger produces mis-taps and visibly slower acquisition — the time to acquire
a target is a function of its distance and its size, and fingers are imprecise
where a mouse is precise.

That last clause is why this gate is scoped to the **input device in use** rather
than applied globally. A 34 px navigation link is correct for a mouse and wrong
for a finger, so the gate reads the ``(pointer: coarse)`` cascade only and leaves
fine-pointer sizing alone. Viewport-width media queries are deliberately NOT
accepted as a touch guarantee: a narrow desktop window matches ``max-width`` and
still has a mouse, while a 1024 px tablet has a finger and does not match it.
Width is a fact about the viewport, never about the hand.

The minimum below is the larger of the two vendor figures (Android 48 dp, iOS
44 pt) so one CSS pixel value satisfies both. Those figures track vendor
documentation and move faster than the perceptual constants around them — this
module is the single place to re-check them against current guidance.

Parsing is a heuristic, in the same spirit as ``check_read_path_aggregates.py``:
it reads the stylesheet's own cascade rather than running a browser engine. It
assumes declaration bodies contain no nested braces, which is true of a hand-
written stylesheet and would need revisiting under a CSS nesting migration.

Usage::

    python scripts/check_touch_targets.py
    python scripts/check_touch_targets.py --stylesheet path/to.css --control button

Exit codes: 0 = every control meets the minimum; 1 = one or more misses.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

#: The stylesheet that defines the browser's whole control surface.
DEFAULT_STYLESHEET = _ROOT / "webapp" / "static" / "design-system.css"

#: Android recommends a 48 dp minimum, iOS a 44 pt minimum; 48 CSS px clears
#: both. Vendor-sourced — re-check against current guidance before asserting it
#: as a specification rather than as this project's chosen floor.
TOUCH_TARGET_MIN_PX = 48

#: CSS root font size assumed when converting ``rem``/``em`` lengths.
_ROOT_FONT_PX = 16.0

#: The control surface this gate is responsible for. Every entry must be a
#: selector the stylesheet actually declares (``uncovered_controls`` proves it),
#: so a renamed component fails loudly instead of silently dropping coverage.
CONTROLS: tuple[str, ...] = (
    "button",
    ".btn",
    "input:not([type])",
    "input[type=text]",
    "input[type=file]",
    "input[type=email]",
    "input[type=password]",
    "input[type=search]",
    "input[type=number]",
    "input[type=tel]",
    "input[type=url]",
    "select",
    "textarea",
    "input[type=checkbox]",
    "input[type=radio]",
    "header nav a",
    ".nav-link-button",
    ".nav-burger",
    ".toggle",
    ".pager a",
    ".footer-nav a",
    ".seg a",
    ".seg label",
    "a.pill",
    "summary",
    ".bottom-nav a",
    ".bottom-nav__more",
    # Corpus layout switch: an <a>, so the `button` rule never bounded it.
    ".view-switch__btn",
    # A sortable column header IS the control that sorts by it.
    ".th-sort",
    # Combobox options are the only way to pick a row from a filtered list.
    ".combobox__opt",
    # Icon-only buttons: both axes bound the target, so both are checked.
    ".toast__close",
    ".upload-item__drop",
)

#: Controls whose hit area is bounded on both axes because they are drawn as a
#: fixed-size box (a tick box, an icon button). Everything else stretches to its
#: content or its container, so height alone bounds the target.
_SQUARE_CONTROLS: frozenset[str] = frozenset(
    {
        "input[type=checkbox]",
        "input[type=radio]",
        ".nav-burger",
    }
)

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_VAR = re.compile(r"var\(\s*(--[\w-]+)\s*\)")
_LENGTH = re.compile(r"^(-?\d*\.?\d+)(px|rem|em)$")


@dataclass(frozen=True)
class Violation:
    """One control that does not reach the minimum on a coarse pointer."""

    selector: str
    axis: str
    actual_px: float
    required_px: int

    def __str__(self) -> str:
        """Render the finding as one reviewable line."""
        actual = "unset" if self.actual_px == 0.0 else f"{self.actual_px:g}px"
        return f"{self.selector}: {self.axis} {actual} < {self.required_px}px"


@dataclass(frozen=True)
class _Rule:
    """One flattened CSS rule plus the media condition it sits under."""

    media: str
    selectors: tuple[str, ...]
    declarations: tuple[tuple[str, str], ...]


def _split_declarations(body: str) -> tuple[tuple[str, str], ...]:
    """Return ``(property, value)`` pairs parsed from a declaration body."""
    pairs = []
    for chunk in body.split(";"):
        prop, sep, value = chunk.partition(":")
        if sep and prop.strip():
            pairs.append((prop.strip().lower(), value.strip()))
    return tuple(pairs)


def _parse(text: str, pos: int = 0, media: str = "", out: list[_Rule] | None = None) -> list[_Rule]:
    """Flatten a stylesheet into rules tagged with their enclosing media query.

    Args:
        text: Stylesheet source with comments already stripped.
        pos: Index to resume scanning from (recursion detail).
        media: The media condition the current block sits under.
        out: Accumulator shared across the recursion.

    Returns:
        Every rule in source order, so a later declaration wins as it would in
        the browser.
    """
    rules = [] if out is None else out
    prelude: list[str] = []
    while pos < len(text):
        char = text[pos]
        if char == "}":
            return rules if out is None else rules
        if char == "{":
            selector = "".join(prelude).strip()
            prelude = []
            if selector.startswith("@"):
                nested = f"{media} {selector}".strip() if media else selector
                pos = _scan_block(text, pos + 1, nested, rules)
                continue
            body, pos = _read_body(text, pos + 1)
            names = tuple(part.strip() for part in selector.split(",") if part.strip())
            rules.append(_Rule(media, names, _split_declarations(body)))
            continue
        prelude.append(char)
        pos += 1
    return rules


def _scan_block(text: str, pos: int, media: str, rules: list[_Rule]) -> int:
    """Parse the inside of an at-rule block and return the index past its ``}``."""
    prelude: list[str] = []
    while pos < len(text):
        char = text[pos]
        if char == "}":
            return pos + 1
        if char == "{":
            selector = "".join(prelude).strip()
            prelude = []
            if selector.startswith("@"):
                nested = f"{media} {selector}".strip() if media else selector
                pos = _scan_block(text, pos + 1, nested, rules)
                continue
            body, pos = _read_body(text, pos + 1)
            names = tuple(part.strip() for part in selector.split(",") if part.strip())
            rules.append(_Rule(media, names, _split_declarations(body)))
            continue
        prelude.append(char)
        pos += 1
    return pos


def _read_body(text: str, pos: int) -> tuple[str, int]:
    """Return the declaration body starting at *pos* and the index past its ``}``."""
    end = text.find("}", pos)
    if end == -1:
        return text[pos:], len(text)
    return text[pos:end], end + 1


def _custom_properties(rules: list[_Rule]) -> dict[str, str]:
    """Return the ``--token: value`` map declared on ``:root``."""
    tokens: dict[str, str] = {}
    for rule in rules:
        if ":root" not in rule.selectors:
            continue
        for prop, value in rule.declarations:
            if prop.startswith("--"):
                tokens[prop] = value
    return tokens


def _to_px(value: str, tokens: dict[str, str], depth: int = 0) -> float | None:
    """Resolve a CSS length to pixels, following ``var()`` indirection.

    Returns ``None`` for values this gate cannot reason about (percentages,
    ``auto``, ``calc()``), which are reported as unbounded rather than guessed at.
    """
    value = value.strip().lower()
    match = _VAR.fullmatch(value)
    if match and depth < 5:
        referenced = tokens.get(match.group(1))
        return None if referenced is None else _to_px(referenced, tokens, depth + 1)
    length = _LENGTH.fullmatch(value)
    if length is None:
        return None
    number = float(length.group(1))
    return number if length.group(2) == "px" else number * _ROOT_FONT_PX


def _applies_on_touch(media: str) -> bool:
    """Whether a rule under *media* is guaranteed to apply to a coarse pointer.

    An unconditioned rule always applies. A ``(pointer: coarse)`` query applies
    by definition. Everything else — width breakpoints above all — is a
    statement about the viewport or the user's motion preference, not about the
    input device, so it cannot discharge the minimum.
    """
    if not media:
        return True
    return "pointer:coarse" in media.replace(" ", "").lower()


def _resolved(rules: list[_Rule], tokens: dict[str, str], selector: str, axis: str) -> float:
    """Return the largest touch extent *selector* is guaranteed on a coarse pointer.

    ``min-<axis>`` and ``<axis>`` are both honoured and the larger wins, because
    either one alone can be what bounds the tappable box.
    """
    best = 0.0
    for prop in (axis, f"min-{axis}"):
        current = 0.0
        for rule in rules:
            if not _applies_on_touch(rule.media) or selector not in rule.selectors:
                continue
            for name, value in rule.declarations:
                if name == prop:
                    pixels = _to_px(value, tokens)
                    current = 0.0 if pixels is None else pixels
        best = max(best, current)
    return best


def _overlay_growth(rules: list[_Rule], tokens: dict[str, str], selector: str) -> float:
    """Return the extra hit area an ``::after`` overlay adds around *selector*.

    A drawn control that must stay small — a tick box, a radio — reaches the
    platform minimum by growing its *active* area rather than its ink, which is
    written as an absolutely positioned pseudo-element with a negative ``inset``.
    A positive inset sits inside the control and adds nothing, so it contributes
    zero rather than a negative extent.
    """
    overlay = f"{selector}::after"
    inset: float | None = None
    positioned = False
    for rule in rules:
        if not _applies_on_touch(rule.media) or overlay not in rule.selectors:
            continue
        for name, value in rule.declarations:
            if name == "position":
                positioned = value.strip().lower() == "absolute"
            elif name == "inset":
                pixels = _to_px(value.split()[0], tokens) if value.split() else None
                inset = inset if pixels is None else pixels
    if not positioned or inset is None or inset >= 0:
        return 0.0
    return abs(inset) * 2


def _load(stylesheet: Path | str | None) -> tuple[list[_Rule], dict[str, str]]:
    """Parse a stylesheet and return its rules plus its custom-property map."""
    path = Path(stylesheet) if stylesheet is not None else DEFAULT_STYLESHEET
    rules = _parse(_COMMENT.sub(" ", path.read_text(encoding="utf-8")))
    return rules, _custom_properties(rules)


def find_violations(
    stylesheet: Path | str | None = None,
    controls: tuple[str, ...] | None = None,
    minimum: int = TOUCH_TARGET_MIN_PX,
) -> list[Violation]:
    """Return every control that misses the touch-target minimum.

    Args:
        stylesheet: Stylesheet to read; defaults to the browser's design system.
        controls: Selectors to check; defaults to :data:`CONTROLS`.
        minimum: Required extent in CSS pixels.

    Returns:
        One :class:`Violation` per failing axis, in inventory order.
    """
    rules, tokens = _load(stylesheet)
    findings: list[Violation] = []
    for selector in controls if controls is not None else CONTROLS:
        square = selector in _SQUARE_CONTROLS
        growth = _overlay_growth(rules, tokens, selector) if square else 0.0
        for axis in ("height", "width") if square else ("height",):
            extent = _resolved(rules, tokens, selector, axis)
            if extent:
                extent += growth
            if extent < minimum:
                findings.append(Violation(selector, axis, extent, minimum))
    return findings


def uncovered_controls(
    stylesheet: Path | str | None = None,
    controls: tuple[str, ...] | None = None,
) -> list[str]:
    """Return inventory selectors the stylesheet never declares.

    A stale entry would let the gate pass by checking a control that no longer
    exists, so drift in either direction is a hard error.
    """
    rules, _ = _load(stylesheet)
    declared = {name for rule in rules for name in rule.selectors}
    wanted = controls if controls is not None else CONTROLS
    return [selector for selector in wanted if selector not in declared]


def main(argv: list[str] | None = None) -> int:
    """Run the gate and report findings.

    Returns:
        0 when every control meets the minimum, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        prog="check_touch_targets",
        description="Fail if a control misses the platform touch-target minimum.",
    )
    parser.add_argument("--stylesheet", default=None, help="stylesheet to check")
    parser.add_argument(
        "--control", action="append", default=None, help="selector to check (repeatable)"
    )
    parser.add_argument(
        "--min", type=int, default=TOUCH_TARGET_MIN_PX, help="required extent in CSS pixels"
    )
    args = parser.parse_args(argv)
    controls = tuple(args.control) if args.control else None

    missing = uncovered_controls(args.stylesheet, controls)
    violations = find_violations(args.stylesheet, controls, args.min)
    checked = len(controls) if controls else len(CONTROLS)

    if missing:
        print(f"touch-target: {len(missing)} declared control(s) absent from the stylesheet")
        for selector in missing:
            print(f"  {selector}: never declared")
    for violation in violations:
        print(f"  {violation}")
    if missing or violations:
        print(f"touch-target: {len(violations)} control(s) below {args.min}px on a coarse pointer")
        return 1
    print(f"touch-target: {checked} control(s) meet the {args.min}px minimum on a coarse pointer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
