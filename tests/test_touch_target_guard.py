"""The touch-target gate: every control must meet the platform minimum on touch.

Guards the guard (``scripts/check_touch_targets.py``). A touch target is the area
a user can tap, which is *not* the same object as the control's drawn
representation — an active area larger than the visual control is the normal way
to reconcile limited screen space with fingers that need comfortable targets.

The rule is input-device-scoped on purpose: a 34 px nav link is correct for a
mouse and wrong for a finger, so the gate reads the coarse-pointer context only
and leaves fine-pointer sizing alone.
"""

from __future__ import annotations

import check_touch_targets as guard


def test_platform_minimum_is_the_larger_of_the_two_vendor_figures():
    """48 covers Android's 48 dp and iOS's 44 pt in one CSS pixel value.

    Sourced from vendor guidance as restated in the 2017 mobile-design corpus;
    the figures track vendor documentation and carry a shorter review interval
    than the perceptual constants, so this constant is the single place to
    re-check them.
    """
    assert guard.TOUCH_TARGET_MIN_PX == 48


def test_guard_is_green_on_the_repository():
    violations = guard.find_violations()
    assert violations == [], f"controls below the touch-target minimum: {violations}"


def test_every_declared_control_is_covered_by_the_stylesheet():
    """The inventory must not name a selector the stylesheet never defines.

    A stale entry would make the gate pass by checking nothing.
    """
    assert guard.uncovered_controls() == []


def test_guard_flags_a_control_below_the_minimum_on_touch(tmp_path):
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "button, .btn { min-height: 2.4rem; }\n",
        encoding="utf-8",
    )
    found = guard.find_violations(stylesheet=css, controls=("button",))
    assert [v.selector for v in found] == ["button"]
    assert found[0].actual_px == 38.4
    assert found[0].required_px == 48


def test_guard_accepts_a_coarse_pointer_override(tmp_path):
    """A fine-pointer size below the minimum is fine when touch raises it."""
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "button, .btn { min-height: 2.4rem; }\n"
        "@media (pointer: coarse) { button, .btn { min-height: var(--tap-min); } }\n",
        encoding="utf-8",
    )
    assert guard.find_violations(stylesheet=css, controls=("button",)) == []


def test_a_width_media_query_does_not_count_as_a_touch_guarantee(tmp_path):
    """Viewport width is not a statement about the input device.

    A narrow desktop window matches ``max-width`` and has a mouse; a 1024 px
    tablet has a finger and does not. Sizing keyed to width therefore cannot
    discharge the platform minimum.
    """
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "header nav a { min-height: 34px; }\n"
        "@media (max-width: 920px) { header nav a { min-height: 46px; } }\n",
        encoding="utf-8",
    )
    found = guard.find_violations(stylesheet=css, controls=("header nav a",))
    assert [v.actual_px for v in found] == [34.0]


def test_guard_checks_both_axes_for_a_square_control(tmp_path):
    """A checkbox is sized on both axes; 48 px tall and 16 px wide still misses."""
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "@media (pointer: coarse) {\n"
        "  input[type=checkbox] { min-height: 48px; width: 1rem; }\n"
        "}\n",
        encoding="utf-8",
    )
    found = guard.find_violations(stylesheet=css, controls=("input[type=checkbox]",))
    assert [(v.axis, v.actual_px) for v in found] == [("width", 16.0)]


def test_an_overlay_enlarges_the_active_area_past_the_drawn_control(tmp_path):
    """The tap area is not the ink: a negative-inset overlay counts toward it.

    This is the prescribed way to reconcile a small drawn control (a tick box
    must stay a tick box) with a finger that needs a comfortable target, so the
    gate has to be able to see it or it would force the wrong fix.
    """
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "@media (pointer: coarse) {\n"
        "  input[type=checkbox] { position: relative; width: 1.35rem; height: 1.35rem; }\n"
        '  input[type=checkbox]::after { content: ""; position: absolute; inset: -13.2px; }\n'
        "}\n",
        encoding="utf-8",
    )
    assert guard.find_violations(stylesheet=css, controls=("input[type=checkbox]",)) == []


def test_an_overlay_that_does_not_reach_the_minimum_still_fails(tmp_path):
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "@media (pointer: coarse) {\n"
        "  input[type=checkbox] { position: relative; width: 1rem; height: 1rem; }\n"
        '  input[type=checkbox]::after { content: ""; position: absolute; inset: -4px; }\n'
        "}\n",
        encoding="utf-8",
    )
    found = guard.find_violations(stylesheet=css, controls=("input[type=checkbox]",))
    assert [v.actual_px for v in found] == [24.0, 24.0]


def test_a_positive_inset_shrinks_rather_than_enlarges(tmp_path):
    """An inset *inside* the control cannot be credited as extra hit area."""
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "@media (pointer: coarse) {\n"
        "  input[type=radio] { position: relative; width: 2rem; height: 2rem; }\n"
        '  input[type=radio]::after { content: ""; position: absolute; inset: 4px; }\n'
        "}\n",
        encoding="utf-8",
    )
    found = guard.find_violations(stylesheet=css, controls=("input[type=radio]",))
    assert [v.actual_px for v in found] == [32.0, 32.0]


def test_guard_resolves_the_token_from_the_root_block(tmp_path):
    """``var(--tap-min)`` is resolved, so lowering the token fails the gate."""
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 30px; }\n"
        "@media (pointer: coarse) { button { min-height: var(--tap-min); } }\n",
        encoding="utf-8",
    )
    found = guard.find_violations(stylesheet=css, controls=("button",))
    assert [v.actual_px for v in found] == [30.0]


def test_an_undeclared_control_is_reported_as_missing(tmp_path):
    """Silence is not compliance: a control with no size at all is a violation."""
    css = tmp_path / "s.css"
    css.write_text(":root { --tap-min: 48px; }\n.thumb-key { color: red; }\n", encoding="utf-8")
    found = guard.find_violations(stylesheet=css, controls=(".thumb-key",))
    assert [v.actual_px for v in found] == [0.0]


def test_comments_do_not_confuse_the_parser(tmp_path):
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "/* button { min-height: 2px; } */\n"
        "@media (pointer: coarse) { button { min-height: 48px; } }\n",
        encoding="utf-8",
    )
    assert guard.find_violations(stylesheet=css, controls=("button",)) == []


def test_main_exits_zero_on_a_clean_stylesheet(tmp_path, capsys):
    css = tmp_path / "s.css"
    css.write_text(
        ":root { --tap-min: 48px; }\n"
        "@media (pointer: coarse) { button { min-height: 48px; } }\n",
        encoding="utf-8",
    )
    assert guard.main(["--stylesheet", str(css), "--control", "button"]) == 0
    assert "touch-target" in capsys.readouterr().out


def test_main_exits_one_and_names_the_control(tmp_path, capsys):
    css = tmp_path / "s.css"
    css.write_text(":root { --tap-min: 48px; }\nbutton { min-height: 20px; }\n", encoding="utf-8")
    assert guard.main(["--stylesheet", str(css), "--control", "button"]) == 1
    assert "button" in capsys.readouterr().out
