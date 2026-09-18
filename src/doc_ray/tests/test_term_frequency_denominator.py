"""BL-204: the term-frequency share denominator must be a maintained scalar.

`_TERM_FREQ_SQL` computed `total_uses` as a live `SUM(uses)` over the whole
`lexeme_stats` table on every call. That is the read-path fact-table aggregate
D-024 forbids, and forced RLS makes it ruinous rather than merely slow: under
the API role with a principal context the aggregate runs an order of magnitude
slower than under the batch role, so on a large corpus every `term_frequency`
call — including single ordinary words — exceeded the statement timeout. The
denominator now comes from `corpus_stats`, the same place migration 142 put the
sentence denominator.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_ADAPTER = (_ROOT / "src" / "doc_ray" / "adapters" / "persistence_read_terms.py").read_text(
    encoding="utf-8"
)

#: Every source file that cites the issue id for this fix. They must agree.
_CITING_SOURCES: tuple[str, ...] = (
    "src/doc_ray/adapters/persistence_read_terms.py",
    "scripts/check_read_path_aggregates.py",
    "db/migrations/152_corpus_stats_total_lexeme_uses.sql",
    "db/migrations/153_schema_version_1_56_0.sql",
    "src/doc_ray/tests/test_term_frequency_denominator.py",
)

_BL_ID_RE = re.compile(r"\bBL-\d{3}\b")


def _statement(name: str) -> str:
    match = re.search(rf'{name}\s*=\s*"""(.*?)"""', _ADAPTER, re.DOTALL)
    assert match, f"{name} not found"
    return match.group(1)


def _fix_release() -> str:
    """The release THIS fix shipped in, derived from its own schema-version marker.

    Deliberately not ``doc_ray.__version__``: that tracks the newest release and
    keeps moving, so pinning to it would make these assertions silently retarget
    a later release that has nothing to do with the denominator.
    """
    markers = sorted((_ROOT / "db" / "migrations").glob("*_schema_version_*.sql"))
    for path in markers:
        # Earliest marker that mentions the column IS the release it shipped in;
        # later releases may mention it again while changing something else.
        if "total_lexeme_uses" in path.read_text(encoding="utf-8"):
            match = re.search(r"schema_version_(\d+)_(\d+)_(\d+)\.sql", path.name)
            assert match, f"unparseable schema-version marker {path.name}"
            return ".".join(match.groups())
    raise AssertionError("no schema-version migration mentions total_lexeme_uses")


def test_the_denominator_is_read_from_the_maintained_rollup() -> None:
    sql = _statement("_TERM_FREQ_SQL")
    assert "corpus_stats" in sql, (
        "total_uses must come from the maintained tenant rollup, not from a live "
        "aggregate over lexeme_stats"
    )


def test_no_live_aggregate_over_the_lexeme_fact_table_remains() -> None:
    """The exact shape that cost 22.9s per call under RLS."""
    sql = _statement("_TERM_FREQ_SQL")
    assert not re.search(r"SUM\s*\(\s*uses\s*\)\s*[^)]*FROM\s+lexeme_stats", sql, re.I), (
        "a read path must not SUM over lexeme_stats; that is the D-024 violation"
    )


def test_the_per_term_join_still_reads_lexeme_stats() -> None:
    """Only the DENOMINATOR moves. Per-term counts are an indexed lookup and stay.

    Removing the join as well would change what the tool answers, not just how
    fast it answers it.
    """
    sql = _statement("_TERM_FREQ_SQL")
    assert "lexeme_stats ls" in sql
    assert "l.lemma = k.key" in sql


def test_the_rollup_column_is_created_and_maintained_by_a_migration() -> None:
    """A scalar nobody maintains is worse than the aggregate it replaced."""
    migrations = sorted((_ROOT / "db" / "migrations").glob("*.sql"))
    text = "\n".join(p.read_text(encoding="utf-8") for p in migrations)
    assert "total_lexeme_uses" in text, "the column must be added by a migration"
    assert re.search(r"CREATE OR REPLACE FUNCTION\s+docray_refresh_corpus_stats", text, re.I), (
        "the refresh function must be redefined to populate the new column, or it "
        "will be NULL for every tenant after the first write"
    )


def test_the_backfill_cannot_be_silently_defeated_by_rls() -> None:
    """Migration 152's backfill was a bare UPDATE and wrote nothing.

    `corpus_stats` carries FORCE ROW LEVEL SECURITY and a single `p_read` SELECT
    policy — there is no UPDATE policy at all — and the migration runner connects
    as `docray`, which is not BYPASSRLS. A plain `UPDATE corpus_stats` therefore
    matches zero rows and raises nothing: the column stayed NULL on production
    after 152 applied, which is the "maintained scalar nobody maintains" case the
    rollup test above warns about, only worse because it looks applied.

    The maintaining function works precisely because it is SECURITY DEFINER owned
    by `docray_batch` (the one BYPASSRLS role), so the backfill must go through
    the same kind of path rather than a bare statement.
    """
    migrations = sorted((_ROOT / "db" / "migrations").glob("*.sql"))
    backfills = [
        p
        for p in migrations
        if "total_lexeme_uses" in p.read_text(encoding="utf-8")
        and re.search(r"UPDATE\s+corpus_stats", p.read_text(encoding="utf-8"), re.I)
    ]
    assert backfills, "no migration backfills total_lexeme_uses"
    text = "\n".join(p.read_text(encoding="utf-8") for p in backfills)
    assert re.search(r"SECURITY\s+DEFINER", text, re.I) and re.search(
        r"OWNER\s+TO\s+docray_batch", text, re.I
    ), (
        "the backfill must run through a SECURITY DEFINER function owned by "
        "docray_batch; a bare UPDATE from the migration role writes nothing "
        "because corpus_stats has FORCE RLS and no UPDATE policy"
    )


def test_the_d024_guard_would_catch_this_class() -> None:
    """The checker passed while a 22.9s read-path aggregate was live.

    Widening it is the part that stops the next one, so assert the guard knows
    about the fact table it missed.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_d024", _ROOT / "scripts" / "check_read_path_aggregates.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Assert MEMBERSHIP, not mere mention: `lexeme_stats` appears in the module
    # docstring, so a substring check passes against the exact checker that let a
    # 22.9s read-path aggregate ship.
    assert "lexeme_stats" in module._FACT_TABLES, (
        "check_read_path_aggregates.py must list lexeme_stats among the tables it "
        "refuses to see aggregated on a read path. It is a rollup by row shape but "
        "a fact table by cardinality: 444,294 rows, one per lexeme per tenant."
    )


# --------------------------------------------------------------------------
# The fix must be traceable: one issue id, one release, and docs that say
# where the denominator now comes from. A correct patch nobody can find again is
# how the same read-path aggregate gets reintroduced.
# --------------------------------------------------------------------------


def test_every_source_cites_the_same_backlog_id() -> None:
    """A fix split across five files must not cite two different issues."""
    cited: dict[str, set[str]] = {}
    for rel in _CITING_SOURCES:
        ids = set(_BL_ID_RE.findall((_ROOT / rel).read_text(encoding="utf-8")))
        if ids:
            cited[rel] = ids
    assert cited, "the fix must cite an issue id somewhere"
    union: set[str] = set().union(*cited.values())
    assert len(union) == 1, f"sources disagree on the issue id for this fix: {cited}"


def test_the_release_is_recorded_in_the_changelog() -> None:
    version = _fix_release()
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^##\s*\[{re.escape(version)}\]", changelog, re.MULTILINE), (
        f"CHANGELOG.md has no ## [{version}] section; check_version_consistency.py "
        "fails the build on exactly this drift"
    )
    heading = re.split(rf"^##\s*\[{re.escape(version)}\]", changelog, flags=re.MULTILINE)[1]
    body = re.split(r"^##\s*\[", heading, flags=re.MULTILINE)[0]
    assert "term_frequency" in body, "the changelog entry must name the tool that was broken"
    assert "corpus_stats" in body, "and where the denominator now comes from"


def test_the_silent_backfill_failure_is_recorded_in_the_changelog() -> None:
    """The column shipped NULL on production; that is a separate operator fact.

    Recorded against whichever release carries the corrective migration, not the
    one that introduced the column — an operator reading 1.56.0 alone would
    otherwise believe the denominator was populated when it was not.
    """
    changelog = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "docray_backfill_total_lexeme_uses" in changelog, (
        "the corrective backfill function must be named in the changelog: a "
        "migration that reported success while writing nothing is exactly what an "
        "operator needs to be told about"
    )


def test_the_public_release_note_exists_and_is_linked() -> None:
    version = _fix_release()
    note = _ROOT / "docs" / "public" / "release-notes" / f"{version}.md"
    assert note.exists(), f"missing public release note {note.relative_to(_ROOT)}"
    index = (_ROOT / "docs" / "public" / "release-notes" / "index.md").read_text(encoding="utf-8")
    assert f"{version}.md" in index, f"no index entry links {version}.md"


def test_the_architecture_doc_states_where_the_share_denominator_comes_from() -> None:
    """§7 already documents the SENTENCE denominator; the uses denominator is the
    one that actually timed out and must be documented beside it."""
    text = (_ROOT / "docs" / "nlp_architecture.md").read_text(encoding="utf-8")
    assert "total_lexeme_uses" in text, (
        "docs/nlp_architecture.md §7 documents corpus_stats.n_sentences as the "
        "sentence denominator but never says where term_frequency's share "
        "denominator comes from — the one that broke"
    )


def test_the_mcp_surface_doc_states_the_share_denominator_is_maintained() -> None:
    text = (_ROOT / "docs" / "mcp-service.md").read_text(encoding="utf-8")
    assert "total_lexeme_uses" in text, (
        "docs/mcp-service.md §3.6 documents the nullable totalSentences denominator "
        "but not the share denominator behind `uses`/`share`"
    )
