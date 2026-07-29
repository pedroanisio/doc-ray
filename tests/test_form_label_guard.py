"""The form-label gate: no control may be named by its placeholder alone.

Guards ``scripts/check_form_labels.py``. Placeholder text is a low-contrast hint
that is hard to read, it disappears the moment the user types, and it is
redundant when it merely repeats the label — so it is a hint, never the name of
the field. Every control a user fills in must carry a real label: a wrapping
``<label>``, a ``<label for>`` pointing at it, or an explicit ARIA name.
"""

from __future__ import annotations

import check_form_labels as guard


def test_guard_is_green_on_the_repository():
    violations = guard.find_violations()
    assert violations == [], f"controls named only by a placeholder: {violations}"


def test_guard_flags_a_placeholder_standing_in_for_a_label(tmp_path):
    (tmp_path / "t.html").write_text(
        '<form><input type="text" name="title" placeholder="title"></form>',
        encoding="utf-8",
    )
    found = guard.find_violations(roots=(tmp_path,))
    assert [(v.name, v.reason) for v in found] == [("title", "placeholder-as-label")]


def test_guard_flags_a_control_with_no_name_at_all(tmp_path):
    (tmp_path / "t.html").write_text('<form><input type="text" name="q"></form>', encoding="utf-8")
    found = guard.find_violations(roots=(tmp_path,))
    assert [(v.name, v.reason) for v in found] == [("q", "unlabelled")]


def test_a_wrapping_label_satisfies_the_rule(tmp_path):
    (tmp_path / "t.html").write_text(
        '<form><label>Tags <input name="tags" placeholder="a, b"></label></form>',
        encoding="utf-8",
    )
    assert guard.find_violations(roots=(tmp_path,)) == []


def test_a_label_for_attribute_satisfies_the_rule(tmp_path):
    (tmp_path / "t.html").write_text(
        '<form><label for="ref">Reference</label>'
        '<input id="ref" name="ref" placeholder="arxiv:2401.00001"></form>',
        encoding="utf-8",
    )
    assert guard.find_violations(roots=(tmp_path,)) == []


def test_an_aria_label_satisfies_the_rule(tmp_path):
    (tmp_path / "t.html").write_text(
        '<form><input name="q" aria-label="Search the corpus" placeholder="find…"></form>',
        encoding="utf-8",
    )
    assert guard.find_violations(roots=(tmp_path,)) == []


def test_a_label_declared_after_the_control_still_counts(tmp_path):
    """Document order is not the rule; the ``for``/``id`` association is."""
    (tmp_path / "t.html").write_text(
        '<form><input id="note" name="note"><label for="note">Note</label></form>',
        encoding="utf-8",
    )
    assert guard.find_violations(roots=(tmp_path,)) == []


def test_hidden_and_button_controls_are_out_of_scope(tmp_path):
    (tmp_path / "t.html").write_text(
        '<form><input type="hidden" name="csrf_token" value="x">'
        '<input type="submit" value="Go">'
        '<button type="submit">Save</button></form>',
        encoding="utf-8",
    )
    assert guard.find_violations(roots=(tmp_path,)) == []


def test_select_and_textarea_are_in_scope(tmp_path):
    (tmp_path / "t.html").write_text(
        '<form><select name="sort"><option>a</option></select>'
        '<textarea name="body"></textarea></form>',
        encoding="utf-8",
    )
    assert sorted(v.name for v in guard.find_violations(roots=(tmp_path,))) == ["body", "sort"]


def test_jinja_control_flow_does_not_break_the_parse(tmp_path):
    (tmp_path / "t.html").write_text(
        "{% extends 'base.html' %}\n"
        "{% block body %}<form>"
        '{% if x %}<label>Email <input type="email" name="email"></label>{% endif %}'
        "</form>{% endblock %}",
        encoding="utf-8",
    )
    assert guard.find_violations(roots=(tmp_path,)) == []


def test_a_jinja_expression_inside_an_attribute_is_tolerated(tmp_path):
    (tmp_path / "t.html").write_text(
        '<form><input name="title" value="{{ doc.title }}" aria-label="{{ label }}"></form>',
        encoding="utf-8",
    )
    assert guard.find_violations(roots=(tmp_path,)) == []


def test_main_exits_one_and_names_the_file(tmp_path, capsys):
    (tmp_path / "bad.html").write_text('<input name="x" placeholder="x">', encoding="utf-8")
    assert guard.main([str(tmp_path)]) == 1
    assert "bad.html" in capsys.readouterr().out


def test_main_exits_zero_when_clean(tmp_path, capsys):
    (tmp_path / "ok.html").write_text('<label>X <input name="x"></label>', encoding="utf-8")
    assert guard.main([str(tmp_path)]) == 0
    assert "form-label" in capsys.readouterr().out
