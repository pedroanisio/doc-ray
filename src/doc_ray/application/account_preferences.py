"""Per-principal UI preferences that follow a user across devices.

These are cosmetic reading defaults — theme, whether the reader opens annotated
or plain, and how many rows a table paginates — persisted server-side so they
apply on any browser the user signs in from. They carry no authorization weight;
document/corpus visibility remains governed entirely by RLS and ACLs.

The service normalizes and clamps every value against the allowed vocabularies
below, so callers (web forms) can pass raw input and rely on a valid result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

#: Allowed theme values. ``system`` defers to the browser's colour-scheme.
THEMES = ("system", "light", "dark")
#: Allowed default reader views.
READER_VIEWS = ("annotated", "plain")
#: Inclusive bounds for table page size.
MIN_ROWS_PER_PAGE = 10
MAX_ROWS_PER_PAGE = 200

DEFAULT_THEME = "system"
DEFAULT_READER_VIEW = "annotated"
DEFAULT_ROWS_PER_PAGE = 50


@dataclass(frozen=True)
class AccountPreferences:
    """A principal's stored UI preferences (defaults when never set)."""

    theme: str = DEFAULT_THEME
    default_reader_view: str = DEFAULT_READER_VIEW
    rows_per_page: int = DEFAULT_ROWS_PER_PAGE

    @property
    def annotated_by_default(self) -> bool:
        """Whether the document reader should open in annotated mode."""
        return self.default_reader_view == "annotated"


class PreferencesRepository(Protocol):
    """Persistence surface required by :class:`PreferencesService`."""

    def get_preferences(self, principal_id: str) -> dict[str, Any] | None:
        """Return the stored preference row for a principal, or ``None``."""
        ...

    def upsert_preferences(
        self,
        *,
        principal_id: str,
        theme: str,
        default_reader_view: str,
        rows_per_page: int,
    ) -> None:
        """Insert or update the principal's preference row."""
        ...


class PreferencesService:
    """Read and write validated per-principal UI preferences."""

    def __init__(self, repository: PreferencesRepository) -> None:
        """Initialize the preferences service."""
        self._repo = repository

    def get(self, principal_id: str) -> AccountPreferences:
        """Return stored preferences, falling back to defaults per field."""
        row = self._repo.get_preferences(principal_id)
        if not row:
            return AccountPreferences()
        return AccountPreferences(
            theme=_coerce_choice(row.get("theme"), THEMES, DEFAULT_THEME),
            default_reader_view=_coerce_choice(
                row.get("default_reader_view"), READER_VIEWS, DEFAULT_READER_VIEW
            ),
            rows_per_page=_clamp_rows(row.get("rows_per_page")),
        )

    def set(
        self,
        *,
        principal_id: str,
        theme: str | None = None,
        default_reader_view: str | None = None,
        rows_per_page: object = None,
    ) -> AccountPreferences:
        """Validate and persist preferences; returns the stored result."""
        prefs = AccountPreferences(
            theme=_coerce_choice(theme, THEMES, DEFAULT_THEME),
            default_reader_view=_coerce_choice(
                default_reader_view, READER_VIEWS, DEFAULT_READER_VIEW
            ),
            rows_per_page=_clamp_rows(rows_per_page),
        )
        self._repo.upsert_preferences(
            principal_id=principal_id,
            theme=prefs.theme,
            default_reader_view=prefs.default_reader_view,
            rows_per_page=prefs.rows_per_page,
        )
        return prefs


def _coerce_choice(value: object, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return text if text in allowed else default


def _clamp_rows(value: object) -> int:
    # bool is an int subclass; reject it so True/False can't read as 1/0.
    if isinstance(value, bool):
        return DEFAULT_ROWS_PER_PAGE
    if isinstance(value, int):
        n = value
    elif isinstance(value, str):
        try:
            n = int(value.strip())
        except ValueError:
            return DEFAULT_ROWS_PER_PAGE
    else:
        return DEFAULT_ROWS_PER_PAGE
    return max(MIN_ROWS_PER_PAGE, min(MAX_ROWS_PER_PAGE, n))


__all__ = [
    "AccountPreferences",
    "PreferencesRepository",
    "PreferencesService",
    "THEMES",
    "READER_VIEWS",
    "MIN_ROWS_PER_PAGE",
    "MAX_ROWS_PER_PAGE",
    "DEFAULT_THEME",
    "DEFAULT_READER_VIEW",
    "DEFAULT_ROWS_PER_PAGE",
]
