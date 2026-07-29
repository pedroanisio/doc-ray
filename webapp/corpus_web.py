"""Corpus-wide browse routes: landing list, bulk actions, upload, jobs, search.

Split out of ``webapp/app.py`` (D-015). Query/GraphQL helpers are resolved
through the ``webapp.app`` module object at call time so the test suite's
monkeypatching of ``q``/``q1``/``gql``/``render_template`` keeps reaching the
routes that moved here.
"""

from __future__ import annotations

import json
from types import ModuleType

from flask import abort, current_app, redirect, request, url_for

from doc_ray.domain.read_consistency import CORPUS_STAT_GUARANTEES, assess
from doc_ray.entrypoints.api.context import MAX_BATCH_IDS

try:
    from webapp.graphql_client import API_URL, api_auth_headers
    from webapp.job_view import decorate_job_rows
    from webapp.previews import document_preview
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from graphql_client import API_URL, api_auth_headers
    from job_view import decorate_job_rows
    from previews import document_preview

_core: ModuleType

#: The corpus can be laid out four ways; the view is a presentation choice over
#: the same rows and the same bulk-selection model (BL-143). Unknown values fall
#: back to ``table`` so a hand-edited or stale URL never 500s.
CORPUS_VIEWS = ("table", "compact", "gallery", "grouped")
CORPUS_DENSITIES = ("comfortable", "compact")
#: ``sort`` is whitelisted to a fixed ORDER-BY fragment — the request value NEVER
#: reaches SQL, so a crafted ``?sort=`` cannot inject. ``n_sentences`` sorts on the
#: SELECT alias; the rest on indexed document columns.
CORPUS_SORTS = {
    "size": "d.char_count DESC NULLS LAST",
    "recent": "d.created_at DESC, d.id",
    "title": "d.title ASC, d.id",
    "sentences": "n_sentences DESC NULLS LAST, d.id",
    "pages": "d.page_count DESC NULLS LAST, d.id",
}
#: The axes a corpus can be sectioned along in the grouped view.
CORPUS_GROUPS = ("none", "type", "language", "status")

_GROUP_KEY = {
    "type": lambda r: str(r.get("doc_type") or "other").lower(),
    "language": lambda r: str(r.get("language") or "unknown").lower(),
    "status": lambda r: str(r.get("status") or "unknown").lower(),
}
_GROUP_LABEL = {
    "type": lambda k: k.upper(),
    "language": lambda k: k.upper(),
    "status": lambda k: k.replace("_", " ").capitalize(),
}


def _group_documents(rows: list[dict], group_by: str) -> list[dict]:
    """Bucket ``rows`` into ordered sections for the grouped view.

    First-seen order is preserved (the rows arrive already sorted), so a group's
    members keep the caller's chosen sort. Each section carries a count and a
    sentence subtotal for its header.
    """
    if group_by == "none" or group_by not in _GROUP_KEY:
        return [
            {
                "key": "all",
                "label": "All documents",
                "count": len(rows),
                "sentences": sum(int(r.get("n_sentences") or 0) for r in rows),
                "rows": rows,
            }
        ]
    keyer, labeler = _GROUP_KEY[group_by], _GROUP_LABEL[group_by]
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(keyer(row), []).append(row)
    return [
        {
            "key": key,
            "label": labeler(key),
            "count": len(members),
            "sentences": sum(int(r.get("n_sentences") or 0) for r in members),
            "rows": members,
        }
        for key, members in buckets.items()
    ]


def corpus_stat_freshness(totals) -> dict:
    """Return the freshness verdict for each figure on the corpus stat band.

    The band is the browser's binding for the ``corpusStats`` GraphQL field, and
    both resolve their promise through the same domain declaration — so a human
    reading the band and an agent reading the API are told the same thing about
    the same number.

    Args:
        totals: The corpus totals row, carrying ``lexemes_computed_at``.

    Returns:
        A mapping of figure name to :class:`~doc_ray.domain.read_consistency.Freshness`.
    """
    computed_at = (totals or {}).get("lexemes_computed_at")
    return {
        declaration.figure: assess(
            declaration,
            computed_at if declaration.figure == "lexemes" else None,
        )
        for declaration in CORPUS_STAT_GUARANTEES
    }


def documents():
    """Render the corpus landing page: a paginated document list plus corpus-wide totals.

    The layout is chosen by query params — ``view`` (table/compact/gallery/grouped),
    ``sort``, ``density``, and ``group_by`` — each whitelisted so a bad value degrades
    to the default rather than erroring, and so ``sort`` can safely drive ORDER BY.
    """
    per_page = _core.current_preferences().rows_per_page
    page = max(1, request.args.get("page", 1, type=int))
    view = request.args.get("view", "table")
    if view not in CORPUS_VIEWS:
        view = "table"
    density = request.args.get("density", "comfortable")
    if density not in CORPUS_DENSITIES:
        density = "comfortable"
    sort = request.args.get("sort", "size")
    if sort not in CORPUS_SORTS:
        sort = "size"
    group_by = request.args.get("group_by", "none")
    if group_by not in CORPUS_GROUPS:
        group_by = "none"
    rows = _core.q(
        f"""
        SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count, d.status,
               d.created_at,
               (SELECT n_sentences FROM document_stats ds WHERE ds.document_id = d.id)
                   AS n_sentences,
               (SELECT string_agg(DISTINCT a.tool, ', ')
                  FROM annotations a WHERE a.document_id = d.id) AS tools
        FROM documents d
        WHERE d.lifecycle_state <> 'deleted'
        ORDER BY {CORPUS_SORTS[sort]}
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"limit": per_page, "offset": (page - 1) * per_page},
    )
    rows = [dict(row, preview=document_preview(row)) for row in rows]
    groups = _group_documents(rows, group_by) if view == "grouped" else None
    # The stat band reads PRECOMPUTED rollups (migration 095), never the live
    # corpus-wide scans it used to: those full-scanned the 17.5M-row
    # lexeme_occurrences table twice per load (~18.7 s vs. a 300 ms SLA).
    #   * documents           — count(*) over the RLS-scoped documents (index-only).
    #   * sentences / occurrences — SUM over document_stats, which is RLS-scoped by
    #     document_id, so the totals are exact for the caller's visible corpus.
    #   * lexemes (distinct)  — NOT summable across documents, so it is a maintained
    #     per-tenant scalar in corpus_stats (itself RLS-scoped to the caller's
    #     tenant). Gated on "sees >= 1 document" so a principal with no visible
    #     corpus reads 0 — preserving the confidentiality guarantee that
    #     tests/test_corpus_stats_rls.py defends (never the global registry count).
    # Soft-deleted documents (lifecycle_state='deleted', BL-059) are excluded from
    # every corpus-browse surface, so the headline counts and the sentence/
    # occurrence rollup sums must not count them either — otherwise "N documents"
    # disagrees with the list below it. The stats sums join back to the (small,
    # RLS-scoped) documents table to drop the deleted set; this stays index-only
    # and does not reintroduce the D-024 fact-table scan.
    #
    # The four figures do NOT share one guarantee — docs is live, sentences and
    # occurrences are pipeline rollups, and the lexeme scalar is debounced and may
    # lag. Its `computed_at` is read alongside the value so the band can declare
    # what it is showing instead of printing four numbers that look alike
    # (doc_ray.domain.read_consistency).
    totals = _core.q1(
        "SELECT (SELECT count(*) FROM documents WHERE lifecycle_state <> 'deleted') docs, "
        "(SELECT coalesce(sum(ds.n_sentences), 0) FROM document_stats ds "
        "   JOIN documents d ON d.id = ds.document_id "
        "   WHERE d.lifecycle_state <> 'deleted') sents, "
        "(SELECT CASE WHEN EXISTS (SELECT 1 FROM documents WHERE lifecycle_state <> 'deleted') "
        "             THEN coalesce((SELECT n_distinct_lexemes FROM corpus_stats), 0) "
        "             ELSE 0 END) lexemes, "
        "(SELECT coalesce(sum(ds.n_lexeme_occurrences), 0) FROM document_stats ds "
        "   JOIN documents d ON d.id = ds.document_id "
        "   WHERE d.lifecycle_state <> 'deleted') occ, "
        "(SELECT computed_at FROM corpus_stats) lexemes_computed_at"
    )
    return _core.render_template(
        "documents.html",
        rows=rows,
        totals=totals,
        stat_freshness=corpus_stat_freshness(totals),
        page=page,
        has_next=len(rows) == per_page,
        view=view,
        density=density,
        sort=sort,
        group_by=group_by,
        groups=groups,
        views=CORPUS_VIEWS,
        sorts=tuple(CORPUS_SORTS),
        group_axes=CORPUS_GROUPS,
        bulk_queued=request.args.get("bulk_queued"),
        bulk_skipped=request.args.get("bulk_skipped"),
        bulk_error=request.args.get("bulk_error"),
    )


def _selected_document_types(ids: list[str]) -> dict[str, str]:
    """Return selected document types keyed by id."""
    rows = _core.q(
        "SELECT id, doc_type FROM documents WHERE id = ANY(%s::uuid[])",
        (ids,),
    )
    return {str(row["id"]): str(row.get("doc_type") or "") for row in rows}


def _enqueue_each(ids: list[str], mutation: str, response_key: str) -> int:
    """Run one per-document GraphQL mutation for each selected id."""
    queued = 0
    for doc_id in ids:
        data = _core.gql(mutation, {"id": doc_id})
        if data.get(response_key):
            queued += 1
    return queued


def documents_bulk_action():
    """Submit corpus-list bulk actions through queued GraphQL mutations."""
    ids = request.form.getlist("document_id")
    action = request.form.get("bulk_action")
    if not ids:
        return redirect(url_for("documents", bulk_error="select at least one document"))
    if len(ids) > MAX_BATCH_IDS:
        return redirect(url_for("documents", bulk_error=f"batch size exceeds {MAX_BATCH_IDS}"))
    try:
        if action == "archive":
            data = _core.gql(
                "mutation($ids: [ID!]!) { bulkArchiveDocuments(documentIds: $ids) { id } }",
                {"ids": ids},
            )
            queued = len(data["bulkArchiveDocuments"])
        elif action == "tag":
            tags = [
                tag.strip() for tag in (request.form.get("tags") or "").split(",") if tag.strip()
            ]
            if not tags:
                return redirect(url_for("documents", bulk_error="enter at least one tag"))
            data = _core.gql(
                "mutation($ids: [ID!]!, $tags: [String!]!) { "
                "bulkTagDocuments(documentIds: $ids, tags: $tags) { id } }",
                {"ids": ids, "tags": tags},
            )
            queued = len(data["bulkTagDocuments"])
        elif action == "reannotate":
            queued = _enqueue_each(
                ids,
                "mutation($id: ID!) { reannotate(documentId: $id) { id } }",
                "reannotate",
            )
        elif action == "reextract":
            doc_types = _selected_document_types(ids)
            pdf_ids = [doc_id for doc_id in ids if doc_types.get(doc_id) == "pdf"]
            queued = _enqueue_each(
                pdf_ids,
                "mutation($id: ID!) { reextractDocument(documentId: $id) { id } }",
                "reextractDocument",
            )
            skipped = len(ids) - len(pdf_ids)
            return redirect(url_for("documents", bulk_queued=queued, bulk_skipped=skipped))
        else:
            return redirect(url_for("documents", bulk_error="choose a bulk action"))
    except Exception as exc:
        return redirect(url_for("documents", bulk_error=str(exc)))
    return redirect(url_for("documents", bulk_queued=queued))


def upload_form():
    """Render the document upload form (EPUB, PDF, TXT, MD, DOCX)."""
    return _core.render_template("upload.html", error=request.args.get("error"))


def upload_submit():
    """Send an uploaded file *or* a remote reference to the ingest mutation.

    Two intake routes, one mutation. A file goes as a GraphQL multipart upload;
    a reference goes as a plain JSON mutation, because there is no file part to
    map and the *worker* performs the fetch — so the browser is never held open
    on a remote download.

    The full pipeline (ingest -> annotate -> canonicalize) is enqueued by
    default; unticking the checkbox ingests only. The browser never touches the
    object store or the queue — the API is the one write path.
    """
    import httpx

    upload = request.files.get("file")
    has_file = upload is not None and bool(upload.filename)
    ref = (request.form.get("ref") or "").strip()
    if has_file and ref:
        return redirect(url_for("upload_form", error="give a file or a reference, not both"))
    if not has_file and not ref:
        return redirect(url_for("upload_form", error="choose a file or paste a reference first"))

    title = request.form.get("title") or None
    pipeline = request.form.get("pipeline") == "on"
    if ref:
        post_kwargs: dict = {
            "json": {
                "query": (
                    "mutation($ref: String, $title: String, $pipeline: Boolean!) {"
                    " ingestDocument(ref: $ref, title: $title, pipeline: $pipeline) { id } }"
                ),
                "variables": {"ref": ref, "title": title, "pipeline": pipeline},
            }
        }
    else:
        operations = {
            "query": (
                "mutation($file: Upload, $title: String, $pipeline: Boolean!) {"
                " ingestDocument(file: $file, title: $title, pipeline: $pipeline) { id } }"
            ),
            "variables": {"file": None, "title": title, "pipeline": pipeline},
        }
        post_kwargs = {
            "data": {
                "operations": json.dumps(operations),
                "map": json.dumps({"0": ["variables.file"]}),
            },
            "files": {"0": (upload.filename, upload.stream, upload.mimetype)},
        }
    try:
        response = httpx.post(
            API_URL,
            headers=api_auth_headers(),
            timeout=120.0,
            **post_kwargs,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            # Our own API's message (validation text) — safe and useful to show.
            raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
        job_id = ((body.get("data") or {}).get("ingestDocument") or {}).get("id")
        if not job_id:
            raise RuntimeError("the API returned no job for this ingest")
    except httpx.HTTPError:
        # Transport-level details (URLs, stack text) belong in the log, not the UI.
        current_app.logger.exception("ingest forwarding failed")
        return redirect(url_for("upload_form", error="ingest API unreachable — is it running?"))
    except Exception as exc:
        current_app.logger.exception("ingest rejected")
        return redirect(url_for("upload_form", error=str(exc)))
    return redirect(url_for("job_status", job_id=job_id))


def job_status(job_id):
    """Render one job's status plus its document's whole pipeline cascade.

    Auto-refreshes while anything is queued or running; once the ingest step
    reports a document id, every job for that document is shown so the chained
    annotate/canonicalize steps are visible as they land.
    """
    job = _core.q1(
        "SELECT id, kind, status, document_id, error, result, created_at, finished_at, "
        "started_at, heartbeat_at, progress_current, progress_total, progress_stage "
        "FROM jobs WHERE id = %s",
        (job_id,),
    )
    if job is None:
        abort(404, "Job not found.")
    doc_id = job["document_id"] or (job["result"] or {}).get("document_id")
    cascade = []
    doc = None
    if doc_id:
        cascade = _core.q(
            "SELECT id, kind, status, error, result, created_at, finished_at, "
            "started_at, heartbeat_at, progress_current, progress_total, progress_stage "
            "FROM jobs WHERE document_id = %s ORDER BY id",
            (doc_id,),
        )
        # The originating ingest job carries document_id NULL (the id lives only
        # in its result) — keep it visible at the head of the cascade.
        if all(j["id"] != job["id"] for j in cascade):
            cascade.insert(0, job)
        doc = _core.q1("SELECT id, title FROM documents WHERE id = %s", (doc_id,))
    active = job["status"] in ("queued", "running") or any(
        j["status"] in ("queued", "running") for j in cascade
    )
    job_rows = decorate_job_rows(cascade or [job])
    return _core.render_template(
        "job.html", job=job, jobs=job_rows, doc=doc, doc_id=doc_id, active=active
    )


def seeding(doc_id):
    """Render the domain-seeding curation screen for one document.

    Suggestions are read directly (like every read on this app); the QID,
    label, and description columns are the audit surface — a curator approves
    or rejects each row before anything reaches the canonical registry.
    """
    doc = _core.q1("SELECT id, title FROM documents WHERE id = %s", (doc_id,))
    if doc is None:
        abort(404, "Document not found.")
    rows = _core.q(
        """
        SELECT id, term, occurrences, source, qid, label, description, entity_type,
               status
        FROM seed_suggestion WHERE document_id = %s
        ORDER BY (qid IS NULL), (status <> 'pending'), occurrences DESC, term
        """,
        (doc_id,),
    )
    counts = {s: 0 for s in ("pending", "approved", "rejected", "seeded")}
    for r in rows:
        counts[r["status"]] += 1
    return _core.render_template(
        "seeding.html",
        doc=doc,
        rows=rows,
        counts=counts,
        notice=request.args.get("notice"),
        error=request.args.get("error"),
    )


def seeding_action(doc_id):
    """Dispatch a curation action to the GraphQL API and redirect back.

    Actions: ``mine`` enqueues candidate mining, ``approve``/``reject`` record
    decisions for the checked rows, ``seed`` enqueues seeding of everything
    approved. All writes go through the API — one write path.
    """
    action = request.form.get("action", "")
    try:
        if action == "mine":
            job = _core.gql(
                "mutation($d: ID!) { suggestSeeds(documentId: $d) { id } }",
                {"d": str(doc_id)},
            )["suggestSeeds"]
            notice = f"mining job {job['id']} queued — refresh shortly"
        elif action in ("approve", "reject"):
            ids = request.form.getlist("sid")
            key = "approveIds" if action == "approve" else "rejectIds"
            n = _core.gql(
                f"mutation($ids: [ID!]) {{ decideSeeds({key}: $ids) }}",
                {"ids": ids},
            )["decideSeeds"]
            notice = f"{n} suggestion(s) {action}d"
        elif action == "seed":
            job = _core.gql(
                "mutation($d: ID!) { seedApproved(documentId: $d) { id } }",
                {"d": str(doc_id)},
            )["seedApproved"]
            notice = f"seeding job {job['id']} queued — refresh shortly"
        else:
            abort(400, "Unknown action.")
    except Exception as exc:
        current_app.logger.exception("seeding action failed")
        return redirect(url_for("seeding", doc_id=doc_id, error=str(exc)))
    return redirect(url_for("seeding", doc_id=doc_id, notice=notice))


def lexemes():
    """Render the paginated lexicon, filtered by lemma, part of speech, and language."""
    term = request.args.get("q", "", type=str).strip()
    pos = request.args.get("pos", "", type=str)
    lang = request.args.get("lang", "", type=str)
    per_page = _core.current_preferences().rows_per_page
    page = max(1, request.args.get("page", 1, type=int))

    params = {
        "q": term,
        "qpat": f"%{term}%",
        "pos": pos,
        "lang": lang,
        "limit": per_page,
        "offset": (page - 1) * per_page,
    }
    # Usage counts come from the precomputed, RLS-tenant-scoped lexeme_stats
    # rollup (migration 098) — ordered by (tenant_id, uses DESC) index — instead of
    # joining the whole 17.5M-row lexeme_occurrences table and GROUP BY-counting
    # every lexeme on each page load (measured ~37 s live). lexeme_stats is
    # RLS-scoped to the caller's tenant, so no explicit tenant predicate is needed.
    rows = _core.q(
        """
        SELECT l.uid, l.lang, l.pos, l.lemma, ls.uses
        FROM lexeme_stats ls
        JOIN lexemes l ON l.uid = ls.lexeme_uid
        WHERE (%(q)s = '' OR l.lemma ILIKE %(qpat)s)
          AND (%(pos)s = '' OR l.pos = %(pos)s)
          AND (%(lang)s = '' OR l.lang = %(lang)s)
        ORDER BY ls.uses DESC, l.lemma
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    # Filter facets: the distinct languages / parts of speech in the shared lexeme
    # registry. Read straight from lexemes (a small, indexed registry) rather than
    # joining lexeme_occurrences just to enumerate a handful of values.
    langs = [r["lang"] for r in _core.q("SELECT DISTINCT lang FROM lexemes ORDER BY lang")]
    poses = [r["pos"] for r in _core.q("SELECT DISTINCT pos FROM lexemes ORDER BY pos")]
    return _core.render_template(
        "lexemes.html",
        rows=rows,
        term=term,
        pos=pos,
        lang=lang,
        langs=langs,
        poses=poses,
        page=page,
        has_next=len(rows) == per_page,
    )


def lexeme(uid):
    """Render one lexeme with its surface forms and keyword-in-context occurrences.

    Args:
        uid: The unique identifier of the lexeme to display.

    Returns:
        The rendered lexeme page, or a 404 tuple when the lexeme is unknown.
    """
    lex = _core.q1("SELECT * FROM lexemes WHERE uid = %s", (uid,))
    if not lex:
        abort(404, "Lexeme not found.")
    # Both snippet queries bound the occurrence scan BEFORE touching
    # full_text: substring() detoasts the whole document text per row, so
    # computing it for every occurrence of a hot lexeme (105k rows measured
    # live) and then keeping 25/80 cost ~10 s per query. The distinct-offsets
    # subquery keeps the forms list stable for lexemes under the cap while
    # never expanding more than 400 snippets.
    forms = _core.q(
        """
        SELECT DISTINCT substring(c.full_text FROM x.char_start + 1
                                  FOR x.char_end - x.char_start) AS form
        FROM (SELECT DISTINCT s.document_id, s.char_start, s.char_end
              FROM lexeme_occurrences o
              JOIN spans s ON s.id = o.span_id
              WHERE o.lexeme_uid = %s LIMIT 400) x
        JOIN document_content c ON c.document_id = x.document_id
        LIMIT 25
        """,
        (uid,),
    )
    n_occ = _core.q1("SELECT count(*) n FROM lexeme_occurrences WHERE lexeme_uid = %s", (uid,))["n"]
    occ = _core.q(
        """
        SELECT x.doc_id, x.title, x.char_start,
               substring(c.full_text FROM greatest(x.char_start - 45, 0) + 1
                         FOR (x.char_end - x.char_start) + 90) AS kwic,
               substring(c.full_text FROM x.char_start + 1
                         FOR x.char_end - x.char_start) AS surface
        FROM (SELECT d.id AS doc_id, d.title, s.char_start, s.char_end
              FROM lexeme_occurrences o
              JOIN annotations a ON a.id = o.annotation_id
              JOIN spans s       ON s.id = o.span_id
              JOIN documents d   ON d.id = a.document_id
              WHERE o.lexeme_uid = %s
              ORDER BY d.title, s.char_start LIMIT 80) x
        JOIN document_content c ON c.document_id = x.doc_id
        ORDER BY x.title, x.char_start
        """,
        (uid,),
    )
    # Split each snippet around the surface form for highlighting.
    for o in occ:
        pre, _, post = (o["kwic"] or "").partition(o["surface"] or "\0")
        o["pre"], o["post"] = pre, post
    return _core.render_template(
        "lexeme.html", lex=lex, forms=[f["form"] for f in forms], occ=occ, n_occ=n_occ
    )


def register(app, core: ModuleType) -> None:
    """Attach the corpus browse routes to *app*, late-binding *core* (D-015 split)."""
    global _core
    _core = core
    app.add_url_rule("/corpus", "documents", documents)
    app.add_url_rule(
        "/documents/bulk", "documents_bulk_action", documents_bulk_action, methods=["POST"]
    )
    app.add_url_rule("/upload", "upload_form", upload_form)
    app.add_url_rule("/upload", "upload_submit", upload_submit, methods=["POST"])
    app.add_url_rule("/job/<job_id>", "job_status", job_status)
    app.add_url_rule("/doc/<uuid:doc_id>/seeding", "seeding", seeding)
    app.add_url_rule(
        "/doc/<uuid:doc_id>/seeding", "seeding_action", seeding_action, methods=["POST"]
    )
    app.add_url_rule("/lexemes", "lexemes", lexemes)
    app.add_url_rule("/lexeme/<uid>", "lexeme", lexeme)
