"""Stage 1.5 — registry-driven term scan (deterministic terminology mentions).

Technical literature carries its vocabulary as lowercase common nouns ("uncial",
"parchment"), which NER never tags — so registry concepts were unreachable by
Stage 2. The term scan closes that gap: scan document text for known form_index
surfaces and emit entity_mention rows (layer 'term'); Stage 2 links them through
the one existing pathway (ambiguous forms still become residue for the frontier).

Pure scanner tests need no DB; integration tests use the live DB (skip-clean) with
nonsense-word forms so the shared registry can never interfere.
"""

from __future__ import annotations

import pytest

from doc_ray.application.term_scan import TermForm, scan_text

# ---------------------------------------------------------------------------
# Pure scanner
# ---------------------------------------------------------------------------


def _surfaces(hits):
    return [(h.char_start, h.char_end, h.surface) for h in hits]


def test_scan_finds_single_and_multi_word_forms_case_insensitively():
    text = "Write in Uncial letters; the half uncial came later. UNCIAL endures."
    hits = scan_text(text, [TermForm("uncial", "CONCEPT"), TermForm("half uncial", "CONCEPT")])
    assert [h.surface for h in hits] == ["Uncial", "half uncial", "UNCIAL"]
    # every hit's offsets index back into the original text exactly
    for h in hits:
        assert text[h.char_start : h.char_end] == h.surface
    assert all(h.entity_type == "CONCEPT" for h in hits)


def test_scan_respects_word_boundaries():
    text = "The uncialesque style is not uncial; 'uncial,' with punctuation, is."
    hits = scan_text(text, [TermForm("uncial", "CONCEPT")])
    # 'uncialesque' must NOT match; the bare and punctuated occurrences must.
    assert len(hits) == 2
    assert all(h.surface == "uncial" for h in hits)


def test_longest_match_wins_on_overlap():
    text = "He mastered the half uncial hand."
    hits = scan_text(text, [TermForm("uncial", "CONCEPT"), TermForm("half uncial", "CONCEPT")])
    # one hit, the longer form — not 'half uncial' plus an inner 'uncial'
    assert _surfaces(hits) == [(16, 27, "half uncial")]


def test_short_stopword_and_nonalpha_forms_are_never_matched():
    text = "The pen was there in 1906 with them."
    forms = [
        TermForm("pen", "CONCEPT"),  # single-word < 4 chars -> ineligible
        TermForm("the", "CONCEPT"),  # short AND a stopword
        TermForm("there", "CONCEPT"),  # >= 4 chars but a common stopword
        TermForm("1906", "CONCEPT"),  # no letters -> ineligible
    ]
    assert scan_text(text, forms) == []


def test_allcaps_acronym_forms_require_exact_case():
    text = "The NASA archive; nasa is lowercase noise."
    hits = scan_text(text, [TermForm("NASA", "ORG")])
    assert _surfaces(hits) == [(4, 8, "NASA")]


def test_multiword_form_matches_across_line_breaks():
    text = "the half\nuncial hand"
    hits = scan_text(text, [TermForm("half uncial", "CONCEPT")])
    assert len(hits) == 1
    assert hits[0].surface == "half\nuncial"


def test_no_forms_yields_no_hits():
    assert scan_text("any text at all", []) == []


# ---------------------------------------------------------------------------
# Integration (live DB, skip-clean) — nonsense vocabulary isolates from the
# shared registry: these forms cannot exist outside this test.
# ---------------------------------------------------------------------------


def _connect_or_skip():
    import _itdb

    try:
        return _itdb.connect()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"DB unreachable: {exc}")


@pytest.fixture
def scan_env():
    from doc_ray.adapters import canonical_store as store

    conn = _connect_or_skip()
    import _itdb

    doc_id = _itdb.make_doc(conn, "A florqin was used on velmurite. The old florqin style endures.")
    cids = {}
    try:
        for name in ("florqin", "velmurite"):
            cid = store.create_canonical(conn, preferred=name, entity_type="CONCEPT")
            store.upsert_form(
                conn,
                form_norm=name,
                form_surface=name,
                canonical_id=cid,
                form_type="name",
                confidence=1.0,
                source_type="seed",
            )
            cids[name] = cid
        yield conn, doc_id, cids
    finally:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        for cid in cids.values():
            conn.execute("DELETE FROM canonical WHERE id = %s", (cid,))
        conn.close()


def _term_mentions(conn, doc_id):
    return conn.execute(
        "SELECT em.id, em.entity_type, sp.char_start, sp.char_end "
        "FROM entity_mention em "
        "JOIN annotations a ON a.id = em.annotation_id "
        "JOIN spans sp ON sp.id = em.span_id "
        "WHERE a.document_id = %s AND a.layer = 'term' ORDER BY sp.char_start",
        (doc_id,),
    ).fetchall()


def test_scan_document_emits_term_mentions_and_stage2_links_them(scan_env):
    from doc_ray.application.canonicalize import autolink_document
    from doc_ray.application.term_scan import scan_document

    conn, doc_id, cids = scan_env
    n = scan_document(conn, doc_id)
    assert n == 3  # florqin x2 + velmurite x1

    mentions = _term_mentions(conn, doc_id)
    assert len(mentions) == 3
    assert all(m["entity_type"] == "CONCEPT" for m in mentions)

    # Stage 2 links them through the ordinary pathway (single candidate, conf 1.0)
    result = autolink_document(conn, doc_id)
    assert result.linked >= 3
    linked = conn.execute(
        "SELECT el.kb_id, count(*) AS n FROM entity_link el "
        "JOIN entity_mention em ON em.id = el.mention_id "
        "JOIN annotations a ON a.id = em.annotation_id "
        "WHERE a.document_id = %s AND el.scheme = 'canonical' GROUP BY el.kb_id",
        (doc_id,),
    ).fetchall()
    by_kb = {r["kb_id"]: r["n"] for r in linked}
    assert by_kb[str(cids["florqin"])] == 2
    assert by_kb[str(cids["velmurite"])] == 1


def test_scan_document_is_idempotent(scan_env):
    from doc_ray.application.term_scan import scan_document

    conn, doc_id, _ = scan_env
    assert scan_document(conn, doc_id) == 3
    assert scan_document(conn, doc_id) == 3  # replace, never accumulate
    assert len(_term_mentions(conn, doc_id)) == 3


def test_canonicalize_job_runs_the_term_scan_before_autolink(scan_env):
    # The worker's canonicalize job must make registry terminology linkable in
    # one pass: scan (Stage 1.5) -> autolink (Stage 2), reported in the result.
    from doc_ray.adapters import queue_postgres as q
    from doc_ray.application.jobs import HANDLERS, run_once
    from doc_ray.domain.jobs import JobStatus

    conn, doc_id, cids = scan_env
    job_id = q.enqueue(conn, "canonicalize", {}, priority=0, document_id=doc_id)
    try:
        done = run_once(conn, None, worker_id="w-test", handlers=HANDLERS)
        assert done is not None and done.id == job_id
        assert q.get(conn, job_id).status is JobStatus.SUCCEEDED
        result = conn.execute("SELECT result FROM jobs WHERE id = %s", (job_id,)).fetchone()[
            "result"
        ]
        assert result["term_mentions"] == 3
        assert result["linked"] >= 3
    finally:
        conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


def test_scan_document_rejects_non_nominal_single_token_hits(scan_env):
    # Polysemous KB aliases fire on verb participles ("...a loose end hanging
    # from..." -> tapestry). Single-token hits must be nominal per the doc's own
    # POS layer; tokens tagged VERB/ADV/etc. are usage, not terminology.
    from doc_ray.application.term_scan import scan_document
    from doc_ray.domain.ulid import new_ulid_str

    conn, doc_id, _ = scan_env
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc_id),
    )
    # 'A florqin was used on velmurite. The old florqin style endures.'
    #    2..9 florqin (make it a VERB)          41..48 florqin (a NOUN)
    for start, end, upos in ((2, 9, "VERB"), (41, 48, "NOUN")):
        span = conn.execute(
            "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s,%s,%s) "
            "ON CONFLICT (document_id, char_start, char_end) DO UPDATE SET char_start = %s "
            "RETURNING id",
            (doc_id, start, end, start),
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO tokens (id, annotation_id, span_id, ordinal, upos) VALUES (%s,%s,%s,0,%s)",
            (new_ulid_str(), ann, span, upos),
        )

    n = scan_document(conn, doc_id)
    assert n == 2  # NOUN florqin + velmurite (no token info -> kept); VERB florqin rejected
    starts = [m["char_start"] for m in _term_mentions(conn, doc_id)]
    assert 2 not in starts
    assert 41 in starts


def test_scan_document_skips_spans_already_covered_by_ner(scan_env):
    from doc_ray.application.term_scan import scan_document
    from doc_ray.domain.ulid import new_ulid_str

    conn, doc_id, _ = scan_env
    # Simulate a Stage-1 NER mention already covering the first 'florqin' (chars 2..9).
    ann = new_ulid_str()
    conn.execute(
        "INSERT INTO annotations (id, document_id, layer, tool) VALUES (%s,%s,'spacy','test')",
        (ann, doc_id),
    )
    span = conn.execute(
        "INSERT INTO spans (document_id, char_start, char_end) VALUES (%s, 2, 9) "
        "ON CONFLICT (document_id, char_start, char_end) DO UPDATE SET char_start = 2 "
        "RETURNING id",
        (doc_id,),
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO entity_mention (id, annotation_id, span_id, entity_type, surface) "
        "VALUES (%s,%s,%s,'MISC','florqin')",
        (new_ulid_str(), ann, span),
    )

    n = scan_document(conn, doc_id)
    assert n == 2  # second florqin + velmurite; the covered one is NER's
    starts = [m["char_start"] for m in _term_mentions(conn, doc_id)]
    assert 2 not in starts
