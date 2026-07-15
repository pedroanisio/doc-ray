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


def documents():
    """Render the corpus landing page: a paginated document list plus corpus-wide totals."""
    per_page = _core.current_preferences().rows_per_page
    page = max(1, request.args.get("page", 1, type=int))
    rows = _core.q(
        """
        SELECT d.id, d.title, d.doc_type, d.language, d.char_count, d.page_count, d.status,
               (SELECT count(*) FROM sentences s WHERE s.document_id = d.id) AS n_sentences,
               (SELECT string_agg(DISTINCT a.tool, ', ')
                  FROM annotations a WHERE a.document_id = d.id) AS tools
        FROM documents d
        ORDER BY d.char_count DESC NULLS LAST
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"limit": per_page, "offset": (page - 1) * per_page},
    )
    rows = [dict(row, preview=document_preview(row)) for row in rows]
    totals = _core.q1(
        "SELECT (SELECT count(*) FROM documents) docs, "
        "(SELECT count(*) FROM sentences) sents, "
        # `lexemes` is a shared registry with NO row-level security (018_rbac.sql
        # groups it with authors/tags), so a bare count(*) leaks the global
        # vocabulary of every tenant's documents to a user who can see none.
        # Count distinct lexemes through lexeme_occurrences, which IS RLS-scoped —
        # matching how the Lexicon page joins — so the card reflects only the
        # caller's visible corpus (0 when they have no documents).
        "(SELECT count(DISTINCT o.lexeme_uid) FROM lexeme_occurrences o) lexemes, "
        "(SELECT count(*) FROM lexeme_occurrences) occ"
    )
    return _core.render_template(
        "documents.html",
        rows=rows,
        totals=totals,
        page=page,
        has_next=len(rows) == per_page,
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
    """Send the uploaded file to the GraphQL ingest mutation and track its job.

    The full pipeline (ingest -> annotate -> canonicalize) is enqueued by
    default; unticking the checkbox uploads ingest-only. The browser never
    touches the object store or the queue — the API is the one write path.
    """
    import httpx

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return redirect(url_for("upload_form", error="choose a file first"))
    operations = {
        "query": (
            "mutation($file: Upload!, $title: String, $pipeline: Boolean!) {"
            " ingestDocument(file: $file, title: $title, pipeline: $pipeline) { id } }"
        ),
        "variables": {
            "file": None,
            "title": request.form.get("title") or None,
            "pipeline": request.form.get("pipeline") == "on",
        },
    }
    try:
        response = httpx.post(
            API_URL,
            data={
                "operations": json.dumps(operations),
                "map": json.dumps({"0": ["variables.file"]}),
            },
            files={"0": (upload.filename, upload.stream, upload.mimetype)},
            headers=api_auth_headers(),
            timeout=120.0,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errors"):
            # Our own API's message (validation text) — safe and useful to show.
            raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
        job_id = ((body.get("data") or {}).get("ingestDocument") or {}).get("id")
        if not job_id:
            raise RuntimeError("the API returned no job for this upload")
    except httpx.HTTPError:
        # Transport-level details (URLs, stack text) belong in the log, not the UI.
        current_app.logger.exception("upload forwarding failed")
        return redirect(url_for("upload_form", error="ingest API unreachable — is it running?"))
    except Exception as exc:
        current_app.logger.exception("upload rejected")
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
    rows = _core.q(
        """
        SELECT l.uid, l.lang, l.pos, l.lemma, count(o.*) AS uses
        FROM lexemes l
        JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
        WHERE (%(q)s = '' OR l.lemma ILIKE %(qpat)s)
          AND (%(pos)s = '' OR l.pos = %(pos)s)
          AND (%(lang)s = '' OR l.lang = %(lang)s)
        GROUP BY l.uid, l.lang, l.pos, l.lemma
        ORDER BY uses DESC, l.lemma
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    langs = [
        r["lang"]
        for r in _core.q(
            """
            SELECT DISTINCT l.lang
            FROM lexemes l
            JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
            ORDER BY l.lang
            """
        )
    ]
    poses = [
        r["pos"]
        for r in _core.q(
            """
            SELECT DISTINCT l.pos
            FROM lexemes l
            JOIN lexeme_occurrences o ON o.lexeme_uid = l.uid
            ORDER BY l.pos
            """
        )
    ]
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


def search():
    """Render full-text sentence search results for the ``q`` query parameter."""
    term = request.args.get("q", "", type=str).strip()
    per_page = 100
    page = max(1, request.args.get("page", 1, type=int))
    rows = []
    has_next = False
    if term:
        rows = _core.q(
            """
            SELECT d.id AS doc_id, d.title, s.ordinal, s.text
            FROM sentences s JOIN documents d ON d.id = s.document_id
            WHERE s.text ILIKE %(term)s
            ORDER BY d.title, s.ordinal LIMIT %(limit)s OFFSET %(offset)s
            """,
            {"term": f"%{term}%", "limit": per_page, "offset": (page - 1) * per_page},
        )
        has_next = len(rows) == per_page
        for r in rows:
            pre, _, post = r["text"].partition(term)
            r["pre"], r["hit"], r["post"] = pre, (term if _ else ""), post
    return _core.render_template("search.html", term=term, rows=rows, page=page, has_next=has_next)


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
    app.add_url_rule("/search", "search", search)
