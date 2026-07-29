"""BL-196 back-fill: repair captions already persisted with model scaffolding.

The sanitiser fixes new captions. The 386 rows already in the corpus still carry
the scaffolding an operator has to read, so they are repaired in place: rows that
recover into prose keep their status with cleaned text, rows with nothing left
are rejected with a recorded reason. Nothing is deleted — a rejected row is still
the audit trail for a paid-for API call.
"""

from __future__ import annotations

from typing import Any

import pytest
import repair_caption_scaffolding as repair


class _Conn:
    """psycopg-shaped double capturing the writes the repair issues."""

    def __init__(self, rows: list[tuple[str, str]], visible: int = 637) -> None:
        self.rows = rows
        self.visible = visible
        self.statements: list[str] = []
        self.updates: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = None) -> _Conn:
        self.statements.append(sql)
        self._last = sql
        if "UPDATE visual_caption" in sql:
            self.updates.append(params)
        return self

    def fetchone(self) -> tuple[Any, ...]:
        return (self.visible,)

    def fetchall(self) -> list[tuple[str, str]]:
        return self.rows

    def close(self) -> None:
        return None


_RECOVERABLE = (
    "id-1",
    "<th noise ,,. </think> The image is a flowchart with three labelled boxes "
    "connected by arrows.",
)
_UNRECOVERABLE = ("id-2", "<th a ,")
_ALREADY_CLEAN = ("id-3", "A photograph of a bridge at dusk with two cyclists crossing.")


def test_batch_role_is_assumed_before_reading_captions() -> None:
    conn = _Conn([_RECOVERABLE])
    repair.repair(conn, dry_run=True)
    assert conn.statements[0] == f"SET ROLE {repair.BATCH_ROLE}"


def test_invisible_corpus_fails_closed() -> None:
    """Zero visible rows under RLS must not be reported as 'nothing to repair'."""
    with pytest.raises(repair.CorpusInvisible):
        repair.repair(_Conn([], visible=0), dry_run=True)


def test_recoverable_rows_keep_their_status_with_cleaned_text() -> None:
    conn = _Conn([_RECOVERABLE])
    result = repair.repair(conn, dry_run=False)
    assert result["cleaned"] == 1
    assert result["rejected"] == 0
    (params,) = conn.updates
    assert params["caption"].startswith("The image is a flowchart")
    assert params["status"] is None, "a recoverable row must not have its status changed"


def test_unrecoverable_rows_are_rejected_with_a_recorded_reason() -> None:
    conn = _Conn([_UNRECOVERABLE])
    result = repair.repair(conn, dry_run=False)
    assert result["rejected"] == 1
    (params,) = conn.updates
    assert params["status"] == "rejected"
    assert repair.REJECTION_REASON in params["error"]


def test_already_clean_rows_are_not_rewritten() -> None:
    """Sanitisation is a no-op on 93.7% of the corpus; those rows must not churn."""
    conn = _Conn([_ALREADY_CLEAN])
    result = repair.repair(conn, dry_run=False)
    assert result["cleaned"] == 0
    assert result["rejected"] == 0
    assert conn.updates == []


def test_dry_run_reports_the_same_counts_but_writes_nothing() -> None:
    conn = _Conn([_RECOVERABLE, _UNRECOVERABLE, _ALREADY_CLEAN])
    result = repair.repair(conn, dry_run=True)
    assert (result["cleaned"], result["rejected"]) == (1, 1)
    assert conn.updates == []
    assert not any("UPDATE" in s for s in conn.statements)


def test_nothing_is_deleted() -> None:
    """A rejected caption is still the audit trail for a paid-for API call."""
    conn = _Conn([_UNRECOVERABLE])
    repair.repair(conn, dry_run=False)
    assert not any("DELETE" in s.upper() for s in conn.statements)


_WHITESPACE_ONLY = (
    "id-4",
    "A photograph of a bridge\n\nat dusk   with two cyclists crossing the span.",
)


def test_whitespace_only_differences_are_not_treated_as_repairs() -> None:
    """Collapsing newlines is not a repair; rewriting those rows is pure churn.

    Measured on the corpus: comparing against the raw string counts 3,788 rows as
    changed, against 386 that actually carry scaffolding.
    """
    conn = _Conn([_WHITESPACE_ONLY])
    result = repair.repair(conn, dry_run=False)
    assert (result["cleaned"], result["rejected"]) == (0, 0)
    assert conn.updates == []


_UNCHANGED_BUT_INVALID = ("id-5", "-" * 80)
_COMMITTED_WITHOUT_SPECIFICITY = ("id-6", "a picture of a thing", "committed")


def test_structurally_invalid_rows_are_rejected_even_when_sanitising_changes_nothing() -> None:
    """A row of dashes needs no cleaning and is still not a caption.

    Found on the corpus: an 80-dash string was *committed* with grounding 0.89.
    Gating the repair on 'did the text change' skipped it entirely.
    """
    conn = _Conn([_UNCHANGED_BUT_INVALID])
    result = repair.repair(conn, dry_run=False)
    assert result["rejected"] == 1
    (params,) = conn.updates
    assert params["status"] == "rejected"


def test_committed_rows_failing_the_specificity_gate_are_demoted_to_review() -> None:
    """Re-scoring the gate needs no evidence text, so stale verdicts are fixable.

    A caption with no numerals, acronyms or proper nouns can never auto-commit
    under the corrected gate; leaving it committed keeps unverified text on the
    reader surface.
    """
    conn = _Conn([_COMMITTED_WITHOUT_SPECIFICITY])
    result = repair.repair(conn, dry_run=False)
    assert result["demoted"] == 1
    (params,) = conn.updates
    assert params["status"] == "review"
