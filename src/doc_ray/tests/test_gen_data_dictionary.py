"""BL-047 — drift gate for the generated ``docs/DATABASE.md``.

The data dictionary is generated from the live schema by
``scripts/gen_data_dictionary.py``; nothing asserted that the committed file was
still current, so a schema change could ship a stale doc (as happened once with a
wrong "N migrations applied" header). These tests cover the pure drift-detection
logic (offline, deterministic); the ``--check`` flag is exercised end-to-end against
the live schema by the CI drift-gate step (``.github/workflows/ci.yml``, after
migrations are applied).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "gen_data_dictionary.py"


def _load():
    """Import the standalone generator script as a module (no DB touched)."""
    spec = importlib.util.spec_from_file_location("gen_data_dictionary", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_in_sync_matches(tmp_path):
    mod = _load()
    p = tmp_path / "D.md"
    p.write_text("alpha\nbeta\n", encoding="utf-8")
    ok, msg = mod._in_sync("alpha\nbeta\n", str(p))
    assert ok and "in sync" in msg


def test_in_sync_detects_line_drift(tmp_path):
    mod = _load()
    p = tmp_path / "D.md"
    p.write_text("alpha\nbeta\n", encoding="utf-8")
    ok, msg = mod._in_sync("alpha\nGAMMA\n", str(p))
    assert not ok
    assert "STALE at line 2" in msg  # actionable: points at the first divergence


def test_in_sync_detects_length_drift(tmp_path):
    mod = _load()
    p = tmp_path / "D.md"
    p.write_text("alpha\n", encoding="utf-8")
    ok, msg = mod._in_sync("alpha\nbeta\n", str(p))
    assert not ok and "STALE" in msg


def test_in_sync_missing_file(tmp_path):
    mod = _load()
    ok, msg = mod._in_sync("x", str(tmp_path / "nope.md"))
    assert not ok and "does not exist" in msg


# --- §5 GROUPS coverage invariant -----------------------------------------
# The drift gate (--check) compares committed text to freshly-generated text,
# both built from the SAME GROUPS list, so a live table omitted from every
# GROUP is invisible to it — exactly how DATABASE.md drifted 16 tables behind.
# _coverage_gap makes that omission a hard error; these cover the pure logic.


def test_coverage_gap_none_when_groups_match_live():
    mod = _load()
    ungrouped, phantom = mod._coverage_gap({"documents", "jobs"}, {"documents", "jobs"})
    assert ungrouped == set() and phantom == set()


def test_coverage_gap_flags_live_table_missing_from_groups():
    mod = _load()
    # a new migration adds `jobs` but nobody grouped it
    ungrouped, phantom = mod._coverage_gap({"documents", "jobs"}, {"documents"})
    assert ungrouped == {"jobs"} and phantom == set()


def test_coverage_gap_flags_grouped_name_with_no_live_table():
    mod = _load()
    # a table was renamed/dropped but its GROUP entry lingers
    ungrouped, phantom = mod._coverage_gap({"documents"}, {"documents", "lexeme_forms"})
    assert ungrouped == set() and phantom == {"lexeme_forms"}


def test_groups_list_has_no_duplicate_tables():
    """A table appearing in two GROUPS would render twice — GROUPS must partition."""
    sys.path.insert(0, str(_ROOT / "scripts"))
    content = importlib.import_module("_data_dictionary_content")
    grouped = [t for _, _, tables in content.GROUPS for t in tables]
    dupes = {t for t in grouped if grouped.count(t) > 1}
    assert not dupes, f"tables listed in more than one GROUP: {sorted(dupes)}"
