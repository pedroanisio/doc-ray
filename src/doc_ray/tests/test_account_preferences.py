"""Unit tests for per-principal UI preferences."""

from __future__ import annotations

from typing import Any

from doc_ray.application.account_preferences import (
    DEFAULT_ROWS_PER_PAGE,
    MAX_ROWS_PER_PAGE,
    MIN_ROWS_PER_PAGE,
    AccountPreferences,
    PreferencesService,
)


class _FakePreferencesRepository:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def get_preferences(self, principal_id: str) -> dict[str, Any] | None:
        row = self.rows.get(principal_id)
        return dict(row) if row is not None else None

    def upsert_preferences(
        self, *, principal_id: str, theme: str, default_reader_view: str, rows_per_page: int
    ) -> None:
        self.rows[principal_id] = {
            "theme": theme,
            "default_reader_view": default_reader_view,
            "rows_per_page": rows_per_page,
        }


def test_get_returns_defaults_when_unset() -> None:
    prefs = PreferencesService(_FakePreferencesRepository()).get("p-1")
    assert prefs == AccountPreferences()
    assert prefs.theme == "system"
    assert prefs.annotated_by_default is True
    assert prefs.rows_per_page == DEFAULT_ROWS_PER_PAGE


def test_set_persists_valid_values() -> None:
    repo = _FakePreferencesRepository()
    svc = PreferencesService(repo)
    result = svc.set(
        principal_id="p-1", theme="dark", default_reader_view="plain", rows_per_page=25
    )
    assert result == AccountPreferences(theme="dark", default_reader_view="plain", rows_per_page=25)
    assert repo.rows["p-1"]["theme"] == "dark"
    assert svc.get("p-1").annotated_by_default is False


def test_set_coerces_unknown_choices_to_defaults() -> None:
    svc = PreferencesService(_FakePreferencesRepository())
    result = svc.set(
        principal_id="p-1", theme="rainbow", default_reader_view="fancy", rows_per_page=25
    )
    assert result.theme == "system"
    assert result.default_reader_view == "annotated"


def test_set_clamps_rows_per_page_bounds() -> None:
    svc = PreferencesService(_FakePreferencesRepository())
    assert svc.set(principal_id="a", rows_per_page=5).rows_per_page == MIN_ROWS_PER_PAGE
    assert svc.set(principal_id="b", rows_per_page=9999).rows_per_page == MAX_ROWS_PER_PAGE
    assert svc.set(principal_id="c", rows_per_page="oops").rows_per_page == DEFAULT_ROWS_PER_PAGE


def test_get_falls_back_per_field_on_corrupt_row() -> None:
    repo = _FakePreferencesRepository()
    repo.rows["p-1"] = {"theme": "dark", "default_reader_view": None, "rows_per_page": None}
    prefs = PreferencesService(repo).get("p-1")
    assert prefs.theme == "dark"
    assert prefs.default_reader_view == "annotated"
    assert prefs.rows_per_page == DEFAULT_ROWS_PER_PAGE
