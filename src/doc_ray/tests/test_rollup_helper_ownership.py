"""A rollup helper that cannot bypass RLS writes nothing and says nothing (BL-206).

Migration 154 moved the tenant rollups onto a delta path: the per-document
helpers read a document's rows, diff them against a snapshot, and nudge
``lexeme_stats`` / ``entity_frequency_stats`` by the difference. They shipped
``SECURITY DEFINER`` owned by ``docray``, which is not ``BYPASSRLS``.

``SECURITY DEFINER`` runs the body as the OWNER, so those helpers execute as
``docray`` with no tenant and no principal. Their *input* query over
``annotations`` / ``lexeme_occurrences`` therefore returns zero rows, the delta
computes to "this document contributes nothing", and ``WHERE diff <> 0`` selects
nothing to write. Nothing is inserted, so no policy is ever evaluated and **no
error is raised**. The helper returns success, the caller then refreshes
``corpus_stats`` correctly as ``docray_batch`` and stamps a fresh ``computed_at``,
and the rollup looks maintained while recomputing the same frozen number.

Observed days after migration 154 applied: ``lexeme_stats`` still held exactly
the row count recorded before the cutover while ``lexeme_occurrences`` had gained
tens of thousands of distinct lexemes — dozens of annotated documents contributed
nothing, and ``corpus_stats.total_lexeme_uses`` ran several percent short. That
scalar is the denominator in ``_TERM_FREQ_SQL``, so every ``term_frequency`` share
was computed against a total that was too small.

Migration 159 addressed a *different* symptom of the same omission — the helpers
were never granted ``EXECUTE`` to ``docray_batch``, so the calls failed loudly
with ``InsufficientPrivilege`` and were logged per document. Granting EXECUTE let
the calls proceed into the silent-zero path above, which is why the worker logs
went quiet without the rollup ever resuming. The grant was necessary and not
sufficient: ownership is the half that makes the body able to *see* its input.

``tests/test_forced_rls.py`` already encodes the structural invariant, but its
query keys on ``NOT rolbypassrls`` and the isolated test database applies
migrations as a superuser — so every function there is superuser-owned and the
check cannot fail no matter what the migrations say. The tests below are written
to hold regardless of who applies the migrations.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _ROOT / "db" / "migrations"

#: Tenant rollups that carry FORCE ROW LEVEL SECURITY with a SELECT policy and no
#: write policy at all. A write reaches them only from a BYPASSRLS identity; from
#: any other it is denied on INSERT and silently matches zero rows on UPDATE and
#: DELETE. Derived by selecting the forced-RLS tables that have no policy with
#: cmd in (ALL, INSERT, UPDATE, DELETE).
_BYPASS_ONLY_WRITE_TABLES = (
    "backups",
    "corpus_stats",
    "entity_frequency_stats",
    "job_telemetry",
    "lexeme_stats",
    "operation_schedule",
)

#: The single identity the schema designates for those writes (migration 106).
_BYPASS_ROLE = "docray_batch"

#: Migration 114 swept every SECURITY DEFINER function that existed *at that
#: moment* onto ``docray_batch``. It is a one-time DO block, not a trigger, so it
#: covers helpers first defined up to and including 114 and nothing after — which
#: is exactly how 154's three new helpers slipped through. Functions first defined
#: later must therefore declare their owner themselves. (``CREATE OR REPLACE``
#: preserves the existing owner, so a pre-114 helper redefined by a later
#: migration keeps the sweep's assignment and stays exempt.)
_OWNERSHIP_SWEEP_MIGRATION = 114

_CREATE_FN = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?P<name>docray_\w+)\s*\(",
    re.IGNORECASE,
)
_OWNER_TO = re.compile(
    r"ALTER\s+FUNCTION\s+(?P<name>docray_\w+)\s*\([^)]*\)\s*OWNER\s+TO\s+(?P<role>\w+)",
    re.IGNORECASE,
)


def _migration_sql() -> list[tuple[str, str]]:
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(_MIGRATIONS_DIR.glob("*.sql"))]


def _migration_number(filename: str) -> int:
    return int(filename.split("_", 1)[0])


def _function_bodies(sql: str) -> list[tuple[str, str]]:
    """Return (function_name, body) for every function defined in *sql*.

    A body runs from its ``CREATE FUNCTION`` to the next one (or end of file),
    which is coarse but sufficient: the properties under test — ``SECURITY
    DEFINER`` and a write to a rollup table — are looked for inside a single
    definition, and definitions are never nested.
    """
    starts = [(m.group("name"), m.start()) for m in _CREATE_FN.finditer(sql)]
    bodies = []
    for index, (name, start) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else len(sql)
        bodies.append((name, sql[start:end]))
    return bodies


def _writes_a_bypass_only_table(body: str) -> str | None:
    """The first bypass-only rollup this body writes, if any."""
    for table in _BYPASS_ONLY_WRITE_TABLES:
        write = re.compile(
            rf"(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+{table}\b",
            re.IGNORECASE,
        )
        if write.search(body):
            return table
    return None


def _owner_assignments() -> dict[str, str]:
    """Every ``ALTER FUNCTION ... OWNER TO <role>`` across the migration corpus.

    Later migrations win, matching the order the runner applies them.
    """
    owners: dict[str, str] = {}
    for _name, sql in _migration_sql():
        for match in _OWNER_TO.finditer(sql):
            owners[match.group("name").lower()] = match.group("role").lower()
    return owners


def _helpers_requiring_an_explicit_owner() -> dict[str, tuple[str, str]]:
    """``{function_name: (migration, rollup_table)}`` for helpers 114 cannot cover.

    A SECURITY DEFINER function first defined after the sweep, whose body writes a
    bypass-only rollup, inherits whoever applied the migration unless it says
    otherwise. That set is the same in every environment because it is derived
    from the migration text, not from a database — which is what lets the two
    tests below agree on production and on the superuser-owned test database.
    """
    required: dict[str, tuple[str, str]] = {}
    first_defined: dict[str, str] = {}
    for migration, sql in _migration_sql():
        for name, body in _function_bodies(sql):
            key = name.lower()
            first_defined.setdefault(key, migration)
            if _migration_number(first_defined[key]) <= _OWNERSHIP_SWEEP_MIGRATION:
                continue
            if "SECURITY DEFINER" not in body.upper():
                continue
            table = _writes_a_bypass_only_table(body)
            if table is not None:
                required[key] = (migration, table)
    return required


def test_every_secdef_helper_writing_a_bypass_only_rollup_is_assigned_the_batch_owner() -> None:
    """The authoring-time guard: ownership must be declared, not inherited.

    This is the check that would have caught migration 154 in review. It reads
    only the migration SQL, so it does not care which role applies it — unlike
    the live-schema invariant in ``test_forced_rls.py``, which the superuser-owned
    test database renders unfalsifiable.

    A ``SECURITY DEFINER`` function that writes one of the bypass-only rollups but
    is never assigned ``OWNER TO docray_batch`` inherits the applying role. On the
    managed cluster that is ``docray``, and the helper then reads its own inputs
    through RLS with no principal — seeing nothing, writing nothing, raising
    nothing.
    """
    owners = _owner_assignments()
    offenders = [
        f"{migration}: {name}() writes {table} as SECURITY DEFINER but is never "
        f"assigned OWNER TO {_BYPASS_ROLE} "
        f"(current assignment: {owners.get(name) or 'none'})"
        for name, (migration, table) in sorted(_helpers_requiring_an_explicit_owner().items())
        if owners.get(name) != _BYPASS_ROLE
    ]

    assert offenders == [], (
        "a SECURITY DEFINER helper that writes a forced-RLS rollup with no write "
        "policy must be explicitly owned by the BYPASSRLS role, or it silently "
        "reads and writes nothing:\n  " + "\n  ".join(offenders)
    )


def test_the_shipped_rollup_helpers_are_owned_by_the_batch_role(pg) -> None:
    """The applied schema must agree with the migration text.

    Asserts the concrete owner rather than "some BYPASSRLS role" on purpose: on
    the isolated test database the applying role is a superuser, which bypasses
    RLS anyway, so a weaker assertion would pass while production stayed broken.
    Requiring ``docray_batch`` exactly means the migration had to say so.

    Scoped to the helpers migration 114's sweep cannot reach, for the same reason
    the sweep exists: everything defined earlier was reassigned in place, and on a
    superuser-owned database it was skipped as already-bypassing. Only the helpers
    that must declare their own owner are portable between the two.
    """
    conn, _created = pg
    required = sorted(_helpers_requiring_an_explicit_owner())
    assert required, "the guard has nothing to check — the extraction rule has drifted"

    conn.execute("RESET ROLE")
    try:
        rows = conn.execute(
            """
            SELECT p.proname AS name, r.rolname AS owner
              FROM pg_proc p
              JOIN pg_roles r ON r.oid = p.proowner
             WHERE p.pronamespace = 'public'::regnamespace
               AND p.proname = ANY(%s::text[])
             ORDER BY 1
            """,
            (required,),
        ).fetchall()
    finally:
        conn.execute("SET ROLE docray_batch")

    offenders = [dict(r) for r in rows if r["owner"] != _BYPASS_ROLE]
    assert offenders == [], (
        "SECURITY DEFINER helpers writing a bypass-only rollup must be owned by "
        f"{_BYPASS_ROLE}: {offenders}"
    )


def test_a_non_bypassrls_definer_writes_nothing_and_raises_nothing(pg) -> None:
    """The failure mode itself: silent, not loud. Built from scratch.

    The superuser-owned test database cannot reproduce this naturally, so this
    constructs the managed-cluster condition directly — a source table whose RLS
    hides every row from a non-BYPASSRLS role, and a rollup table forced with a
    SELECT-only policy — and runs the same shape of aggregate the real helpers do.

    The assertion that matters is not that the write fails. It is that it
    *succeeds while writing nothing*: the helper's input is empty, so there are no
    rows to insert, no policy is evaluated, and the caller has nothing to catch.
    That is why three days of drift produced no log line anywhere.
    """
    conn, _created = pg
    conn.execute("RESET ROLE")
    tag = uuid.uuid4().hex[:10]
    probe = f"probe_owner_{tag}"
    src, rollup, fn = f"src_{tag}", f"rollup_{tag}", f"refresh_{tag}"
    try:
        conn.execute(f"CREATE ROLE {probe} NOLOGIN NOSUPERUSER NOBYPASSRLS")
        # ALTER ... OWNER TO requires the incoming owner to hold CREATE on the
        # schema that will contain the object.
        conn.execute(f"GRANT CREATE, USAGE ON SCHEMA public TO {probe}")
        conn.execute(f"CREATE TABLE {src} (id int PRIMARY KEY, k text NOT NULL)")
        conn.execute(f"CREATE TABLE {rollup} (k text PRIMARY KEY, uses bigint NOT NULL)")
        conn.execute(f"INSERT INTO {src} VALUES (1,'alpha'), (2,'alpha'), (3,'beta')")
        conn.execute(f"GRANT SELECT ON {src} TO {probe}, {_BYPASS_ROLE}")
        conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {rollup} TO {probe}, {_BYPASS_ROLE}")

        # The source hides everything from a role with no principal, exactly as
        # `annotations` / `lexeme_occurrences` do for `docray`.
        conn.execute(f"ALTER TABLE {src} ENABLE ROW LEVEL SECURITY")
        conn.execute(f"ALTER TABLE {src} FORCE ROW LEVEL SECURITY")
        conn.execute(f"CREATE POLICY p_read ON {src} FOR SELECT USING (false)")
        # The rollup is readable but writable only by a BYPASSRLS identity.
        conn.execute(f"ALTER TABLE {rollup} ENABLE ROW LEVEL SECURITY")
        conn.execute(f"ALTER TABLE {rollup} FORCE ROW LEVEL SECURITY")
        conn.execute(f"CREATE POLICY p_read ON {rollup} FOR SELECT USING (true)")

        conn.execute(
            f"CREATE FUNCTION {fn}() RETURNS void LANGUAGE sql SECURITY DEFINER AS $$ "
            f"  INSERT INTO {rollup} (k, uses) "
            f"  SELECT k, count(*)::bigint FROM {src} GROUP BY k "
            f"  ON CONFLICT (k) DO UPDATE SET uses = EXCLUDED.uses $$"
        )
        conn.execute(f"GRANT EXECUTE ON FUNCTION {fn}() TO {_BYPASS_ROLE}")

        # 1) Non-BYPASSRLS owner — migration 154 as shipped. The body reads the
        #    source through RLS, sees nothing, and writes nothing. No exception.
        conn.execute(f"ALTER FUNCTION {fn}() OWNER TO {probe}")
        conn.execute(f"SELECT {fn}()")  # must NOT raise — that is the whole defect
        rows = conn.execute(f"SELECT count(*) AS n FROM {rollup}").fetchone()["n"]
        assert rows == 0, (
            "a non-BYPASSRLS definer must be shown writing nothing; if this ever "
            "raises or writes, the silent-drift mechanism has changed"
        )

        # 2) BYPASSRLS owner — the fix. Same call, same grants, real rows.
        conn.execute(f"ALTER FUNCTION {fn}() OWNER TO {_BYPASS_ROLE}")
        conn.execute(f"SELECT {fn}()")
        result = conn.execute(f"SELECT k, uses FROM {rollup} ORDER BY k").fetchall()
        assert [(r["k"], r["uses"]) for r in result] == [("alpha", 2), ("beta", 1)], (
            "handing the helper the BYPASSRLS owner must let it see its input"
        )
    finally:
        conn.execute("RESET ROLE")
        conn.execute(f"DROP FUNCTION IF EXISTS {fn}() CASCADE")
        conn.execute(f"DROP TABLE IF EXISTS {src} CASCADE")
        conn.execute(f"DROP TABLE IF EXISTS {rollup} CASCADE")
        # The schema grant is a dependency of the role; DROP ROLE fails without this.
        conn.execute(f"REVOKE ALL ON SCHEMA public FROM {probe}")
        conn.execute(f"DROP ROLE IF EXISTS {probe}")
        conn.execute("SET ROLE docray_batch")
