"""Stage-3 resolution service with a STUB resolver (live DB, skip w/o DB, no Claude).

Proves the full loop: autolink leaves a residue -> the resolver's verdicts are
applied under the threshold policy (NEW mints a canonical, abstain/low-confidence
queues review) -> the Stage-6 write-back closes the flywheel, so re-running the
deterministic Stage-2 pass now resolves the previously-novel form with no resolver.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_ray.adapters import canonical_store as store
from doc_ray.application.candidate_generation import candidates_for_form
from doc_ray.application.canonicalize import autolink_document
from doc_ray.application.resolution import resolve_residue
from doc_ray.domain.canonical_norm import form_norm
from doc_ray.domain.ulid import new_ulid_str
from doc_ray.ports.resolver import Candidate, Resolution, ResolutionKind

_MIGRATIONS = Path(__file__).resolve().parents[3] / "db" / "migrations"
_FILES = ("021_canonical.sql", "022_form_index.sql", "023_resolution.sql")
_TEXT = "Elena founded a startup in Berlin."  # 'Elena' = [0,5), 'Berlin' = [27,33)


class _StubResolver:
    """Scripted EntityResolver: maps surface -> Resolution; records its calls."""

    name = "stub/test"

    def __init__(self, script: dict[str, Resolution]) -> None:
        self._script = script
        self.calls: list[tuple[str, str]] = []

    def resolve(self, mention: str, sentence: str, candidates: list[Candidate]) -> Resolution:
        self.calls.append((mention, sentence))
        return self._script.get(mention, Resolution(kind=ResolutionKind.ABSTAIN))


def _connect_or_skip():
    import _itdb

    try:
        conn = _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")
    for name in _FILES:
        conn.execute((_MIGRATIONS / name).read_text())
    return conn


def _seed_doc_with_mentions(conn):
    import _itdb

    doc_id = _itdb.make_doc(conn, _TEXT)
    aid = _itdb.make_annotation(conn, doc_id, "ner", "test")
    elena, berlin = new_ulid_str(), new_ulid_str()
    for mid, (s, e), etype in ((elena, (0, 5), "PER"), (berlin, (27, 33), "GPE")):
        span = _itdb.span_of(conn, doc_id, s, e)
        conn.execute(
            "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type) "
            "VALUES (%s, %s, %s, %s)",
            (mid, aid, span, etype),
        )
    return doc_id, elena, berlin


def test_new_entity_commits_and_closes_the_flywheel():
    conn = _connect_or_skip()
    docs: list[str] = []
    canon: list[int] = []
    try:
        doc_id, elena_m, berlin_m = _seed_doc_with_mentions(conn)
        docs.append(doc_id)

        # Both mentions are novel -> everything is residue with span offsets.
        result = autolink_document(conn, doc_id)
        assert result.linked == 0 and len(result.residue) == 2
        assert all(r.char_end > r.char_start for r in result.residue)

        stub = _StubResolver(
            {
                "Elena": Resolution(
                    kind=ResolutionKind.NEW,
                    confidence=0.95,
                    new_preferred="Elena Fischer",
                    harvested_aliases=("E. Fischer",),
                ),
                # 'Berlin' scripted to abstain -> must land in review, not links.
            }
        )
        outcome = resolve_residue(conn, stub, doc_id, result.residue)
        assert (outcome.linked, outcome.created, outcome.review) == (0, 1, 1)

        # The resolver saw the sentence context (Stage 1 offsets -> sentence join).
        assert any("Elena founded" in sentence for _, sentence in stub.calls)

        # NEW committed: canonical minted, mention linked, forms written back.
        cid = store.canonical_for_mention(conn, elena_m)
        assert cid is not None
        canon.append(cid)
        forms = store.forms_for(conn, cid)
        surfaces = {f["form_surface"] for f in forms}
        assert {"Elena Fischer", "Elena", "E. Fischer"} <= surfaces

        # Abstain routed to review with the audit trail, never committed (PALS).
        assert store.canonical_for_mention(conn, berlin_m) is None
        row = conn.execute(
            "SELECT status, reason FROM resolution_review WHERE mention_id = %s", (berlin_m,)
        ).fetchone()
        assert row is not None and row["status"] == "pending"
        assert "stub/test" in row["reason"]

        # THE FLYWHEEL: the previously-novel form now resolves by index lookup alone.
        blocking = candidates_for_form(conn, "elena")
        assert blocking.auto_link_id == cid
    finally:
        for d in docs:
            conn.execute("DELETE FROM documents WHERE id = %s", (d,))
        for c in canon:
            conn.execute("DELETE FROM canonical WHERE id = %s", (c,))
        conn.close()


def test_new_entity_uses_resolver_proposed_type_over_mention_type():
    # The live run minted Clover as GPE because spaCy mistyped the mention; the
    # resolver's proposed type must win over the mention's NER type.
    conn = _connect_or_skip()
    docs: list[str] = []
    canon: list[int] = []
    try:
        doc_id, elena_m, _berlin_m = _seed_doc_with_mentions(conn)
        docs.append(doc_id)
        result = autolink_document(conn, doc_id)
        elena_res = next(r for r in result.residue if r.surface == "Elena")
        assert elena_res.entity_type == "PER"  # what spaCy said

        stub = _StubResolver(
            {
                "Elena": Resolution(
                    kind=ResolutionKind.NEW,
                    confidence=0.95,
                    new_preferred="Elena the Goat",
                    new_entity_type="ANIMAL",  # the resolver's correction
                )
            }
        )
        resolve_residue(conn, stub, doc_id, [elena_res])
        cid = store.canonical_for_mention(conn, elena_m)
        assert cid is not None
        canon.append(cid)
        row = conn.execute("SELECT entity_type FROM canonical WHERE id = %s", (cid,)).fetchone()
        assert row["entity_type"] == "ANIMAL"  # not 'PER'
    finally:
        for d in docs:
            conn.execute("DELETE FROM documents WHERE id = %s", (d,))
        for c in canon:
            conn.execute("DELETE FROM canonical WHERE id = %s", (c,))
        conn.close()


def test_non_referential_types_never_reach_the_frontier():
    # CARDINAL/ORDINAL/DATE/... mentions are NER noise, not entities: they must be
    # skipped before the resolver (cost control), not linked, not queued for review.
    conn = _connect_or_skip()
    docs: list[str] = []
    try:
        import _itdb

        doc_id = _itdb.make_doc(conn, "Two pigs met on Sunday. Elena watched.")
        docs.append(doc_id)
        aid = _itdb.make_annotation(conn, doc_id, "ner", "test")
        specs = [((0, 3), "CARDINAL"), ((16, 22), "DATE"), ((24, 29), "PER")]
        mention_ids = []
        for (s, e), etype in specs:
            span = _itdb.span_of(conn, doc_id, s, e)
            mid = new_ulid_str()
            conn.execute(
                "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type) "
                "VALUES (%s, %s, %s, %s)",
                (mid, aid, span, etype),
            )
            mention_ids.append(mid)

        result = autolink_document(conn, doc_id)
        assert len(result.residue) == 3

        stub = _StubResolver({})  # everything it IS asked about -> abstain -> review
        outcome = resolve_residue(conn, stub, doc_id, result.residue)

        surfaces_seen = [m for m, _ in stub.calls]
        assert "Elena" in surfaces_seen  # referential type went through
        assert "Two" not in surfaces_seen and "Sunday" not in surfaces_seen  # filtered
        assert outcome.skipped == 2
        assert outcome.review == 1  # only Elena's abstain landed in review
        n_review = conn.execute(
            "SELECT count(*) n FROM resolution_review WHERE mention_id = ANY(%s)",
            (mention_ids,),
        ).fetchone()["n"]
        assert n_review == 1
    finally:
        for d in docs:
            conn.execute("DELETE FROM documents WHERE id = %s", (d,))
        conn.close()


def test_select_at_threshold_commits_and_writes_back_the_surface_alias():
    # The SELECT commit path (resolution.py's `elif` companion to NEW) had no
    # direct test: an existing, low-confidence-seeded canonical is selected at
    # a confidence that clears the threshold -> the mention links immediately
    # and the flywheel closes at the new, higher confidence.
    conn = _connect_or_skip()
    docs: list[str] = []
    canon: list[int] = []
    try:
        cid = store.create_canonical(
            conn, preferred="Berlin", entity_type="PLACE", wikidata_id="Q-res-berlin-2"
        )
        canon.append(cid)
        store.upsert_form(
            conn,
            form_norm=form_norm("Berlin"),
            form_surface="Berlin",
            canonical_id=cid,
            form_type="name",
            confidence=0.5,  # below AUTO_LINK_THRESHOLD -> residue with candidates
        )
        doc_id, _elena_m, berlin_m = _seed_doc_with_mentions(conn)
        docs.append(doc_id)

        result = autolink_document(conn, doc_id)
        berlin_res = next(r for r in result.residue if r.surface == "Berlin")

        stub = _StubResolver(
            {"Berlin": Resolution(kind=ResolutionKind.SELECT, canonical_id=cid, confidence=0.95)}
        )
        outcome = resolve_residue(conn, stub, doc_id, [berlin_res])
        assert (outcome.linked, outcome.created, outcome.review) == (1, 0, 0)
        assert store.canonical_for_mention(conn, berlin_m) == cid  # committed

        blocking = candidates_for_form(conn, "Berlin")
        assert blocking.auto_link_id == cid
    finally:
        for d in docs:
            conn.execute("DELETE FROM documents WHERE id = %s", (d,))
        for c in canon:
            conn.execute("DELETE FROM canonical WHERE id = %s", (c,))
        conn.close()


def test_low_confidence_select_goes_to_review_not_link():
    conn = _connect_or_skip()
    docs: list[str] = []
    canon: list[int] = []
    try:
        # Seed a canonical WITH a low-confidence form so Stage 2 yields a candidate
        # but no auto-link; the stub then selects it below the commit threshold.
        cid = store.create_canonical(
            conn, preferred="Berlin", entity_type="PLACE", wikidata_id="Q-res-berlin"
        )
        canon.append(cid)
        store.upsert_form(
            conn,
            form_norm=form_norm("Berlin"),
            form_surface="Berlin",
            canonical_id=cid,
            form_type="name",
            confidence=0.5,  # below AUTO_LINK_THRESHOLD -> residue with candidates
        )
        doc_id, _elena_m, berlin_m = _seed_doc_with_mentions(conn)
        docs.append(doc_id)

        result = autolink_document(conn, doc_id)
        berlin_res = next(r for r in result.residue if r.surface == "Berlin")
        assert [c.canonical_id for c in berlin_res.candidates] == [cid]

        stub = _StubResolver(
            {"Berlin": Resolution(kind=ResolutionKind.SELECT, canonical_id=cid, confidence=0.4)}
        )
        outcome = resolve_residue(conn, stub, doc_id, [berlin_res])
        assert (outcome.linked, outcome.created, outcome.review) == (0, 0, 1)
        assert store.canonical_for_mention(conn, berlin_m) is None  # not committed
        row = conn.execute(
            "SELECT proposed_canonical_id, confidence FROM resolution_review WHERE mention_id = %s",
            (berlin_m,),
        ).fetchone()
        assert row is not None
        assert row["proposed_canonical_id"] == cid  # the proposal is preserved for the human
    finally:
        for d in docs:
            conn.execute("DELETE FROM documents WHERE id = %s", (d,))
        for c in canon:
            conn.execute("DELETE FROM canonical WHERE id = %s", (c,))
        conn.close()
