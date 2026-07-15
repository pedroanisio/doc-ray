"""Single-document routes: reader page, reprocess controls, metadata, streaming.

Split out of ``webapp/app.py`` (D-015). Query/GraphQL helpers are resolved
through the ``webapp.app`` module object at call time so the test suite's
monkeypatching of ``q``/``q1``/``gql``/``render_template`` keeps reaching the
routes that moved here.
"""

from __future__ import annotations

from types import ModuleType

from flask import Response, abort, current_app, redirect, request, stream_with_context, url_for

try:
    from webapp.collections_web import available_manual_collections
    from webapp.document_metadata import submit_metadata_update
    from webapp.graphql_client import API_URL, api_auth_headers
    from webapp.job_view import is_active_job, summarize_outcome, summarize_progress
    from webapp.overlay_web import original_markup, original_overlay
    from webapp.previews import document_preview
    from webapp.reader_blocks import attach_page_blocks, latest_annotation_state
except ModuleNotFoundError:  # pragma: no cover - direct ``python webapp/app.py`` path.
    from collections_web import available_manual_collections
    from document_metadata import submit_metadata_update
    from graphql_client import API_URL, api_auth_headers
    from job_view import is_active_job, summarize_outcome, summarize_progress
    from overlay_web import original_markup, original_overlay
    from previews import document_preview
    from reader_blocks import attach_page_blocks, latest_annotation_state

_core: ModuleType

_REPROCESS_CONFIG = {
    "reextract": {
        "label": "Re-extract",
        "description": "Rebuild text and page geometry from the original PDF.",
        "mutation": "mutation($id: ID!) { reextractDocument(documentId: $id) { id } }",
        "response_key": "reextractDocument",
        "active_kinds": {"reextract"},
        "pdf_only": True,
    },
    "annotate": {
        "label": "Re-annotate",
        "description": (
            "Run full annotation (tokens, part-of-speech, named entities) again. "
            "This deletes and replaces entity mentions — re-run Re-canonicalize "
            "afterward if this document has been canonicalized."
        ),
        "mutation": "mutation($id: ID!) { annotateDocument(documentId: $id) { id } }",
        "response_key": "annotateDocument",
        "active_kinds": {"annotate"},
        "pdf_only": False,
    },
    "reannotate": {
        "label": "Refresh word frequency",
        "description": (
            "Recompute word-frequency/lexicon data from the current text, without "
            "re-running full annotation. Does not touch entities or part-of-speech."
        ),
        "mutation": "mutation($id: ID!) { reannotate(documentId: $id) { id } }",
        "response_key": "reannotate",
        "active_kinds": {"reannotate"},
        "pdf_only": False,
    },
    "semantic_enrich": {
        "label": "Re-enrich",
        "description": "Recompute deterministic semantic enrichment.",
        "mutation": "mutation($id: ID!) { semanticEnrichDocument(documentId: $id) { id } }",
        "response_key": "semanticEnrichDocument",
        "active_kinds": {"semantic_enrich"},
        "pdf_only": False,
    },
    "canonicalize": {
        "label": "Re-canonicalize",
        "description": "Re-run deterministic entity canonicalization.",
        "mutation": "mutation($id: ID!) { canonicalizeDocument(documentId: $id) { id } }",
        "response_key": "canonicalizeDocument",
        "active_kinds": {"canonicalize"},
        "pdf_only": False,
    },
}


def active_reprocess_jobs(doc_id: str) -> dict[str, dict]:
    """Return active pipeline jobs keyed by reprocess step.

    A running job whose heartbeat went stale (worker killed mid-run) is
    excluded — it must not block re-running the step it will never finish.
    """
    rows = _core.q(
        """
        SELECT id, kind, status, progress_current, progress_total, progress_stage,
               started_at, heartbeat_at
        FROM jobs
        WHERE document_id = %s AND status IN ('queued', 'running')
        ORDER BY id DESC
        """,
        (doc_id,),
    )
    active: dict[str, dict] = {}
    for row in rows:
        if not is_active_job(row):
            continue
        for step, cfg in _REPROCESS_CONFIG.items():
            if row.get("kind") in cfg["active_kinds"] and step not in active:
                active[step] = row
    return active


def failed_reprocess_jobs(doc_id: str) -> dict[str, dict]:
    """Return the most recent job per reprocess step, if that attempt failed.

    ``active_reprocess_jobs`` only sees queued/running jobs, so a step whose
    last attempt permanently failed (retries exhausted, or reclaimed after a
    stale heartbeat) looked identical to a step that was never run — the
    reprocess control just offered to run it again with no explanation. A
    step is only reported here when its single most recent job (of any
    status) is the failed one; once a later attempt succeeds, the failure
    is superseded and stops showing.
    """
    rows = _core.q(
        """
        SELECT id, kind, status, attempts, max_attempts, error
        FROM jobs
        WHERE document_id = %s
        ORDER BY id DESC
        """,
        (doc_id,),
    )
    latest_seen: set[str] = set()
    failed: dict[str, dict] = {}
    for row in rows:
        for step, cfg in _REPROCESS_CONFIG.items():
            if row.get("kind") not in cfg["active_kinds"] or step in latest_seen:
                continue
            latest_seen.add(step)
            if row.get("status") == "failed":
                failed[step] = row
    return failed


def reprocess_steps(doc: dict, doc_id: str) -> list[dict]:
    """Build document-page reprocess controls from live job state."""
    active_jobs = active_reprocess_jobs(doc_id)
    failed_jobs = failed_reprocess_jobs(doc_id)
    is_pdf = str(doc.get("doc_type") or "").lower() == "pdf"
    steps = []
    for step, cfg in _REPROCESS_CONFIG.items():
        available = is_pdf or not cfg["pdf_only"]
        active = active_jobs.get(step)
        failed = failed_jobs.get(step) if not active else None
        steps.append(
            {
                "step": step,
                "label": cfg["label"],
                "description": cfg["description"],
                "available": available,
                "active": active,
                "active_status": active.get("status") if active else None,
                "progress": summarize_progress(active) if active else None,
                "outcome": summarize_outcome(failed) if failed else None,
                "attempts": failed.get("attempts") if failed else None,
                "max_attempts": failed.get("max_attempts") if failed else None,
            }
        )
    return steps


def document(doc_id):
    """Render a single document with metadata, POS stats, and annotated sentences."""
    doc = _core.q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if not doc:
        abort(404, "Document not found.")

    authors = _core.q(
        "SELECT a.name FROM document_authors da JOIN authors a ON a.id = da.author_id "
        "WHERE da.document_id = %s ORDER BY da.position",
        (str(doc_id),),
    )
    tags = _core.q(
        "SELECT t.tag FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
        "WHERE dt.document_id = %s ORDER BY t.tag",
        (str(doc_id),),
    )
    pos = _core.q(
        """
        SELECT l.pos, count(*) n
        FROM lexeme_occurrences o
        JOIN annotations a ON a.id = o.annotation_id
        JOIN lexemes l     ON l.uid = o.lexeme_uid
        WHERE a.document_id = %s
        GROUP BY l.pos ORDER BY n DESC
        """,
        (str(doc_id),),
    )
    top_verbs = _core.q(
        """
        SELECT l.uid, l.lemma, count(*) n
        FROM lexeme_occurrences o
        JOIN annotations a ON a.id = o.annotation_id
        JOIN lexemes l     ON l.uid = o.lexeme_uid
        WHERE a.document_id = %s AND l.pos = 'VERB'
        GROUP BY l.uid, l.lemma ORDER BY n DESC LIMIT 15
        """,
        (str(doc_id),),
    )
    per_page = 40
    page = max(1, request.args.get("page", 1, type=int))
    view = request.args.get("view")
    original_view = view == "original"
    full_view = view == "all"
    overlay_mode = request.args.get("overlay", "grammar")
    if overlay_mode not in {"grammar", "entities"}:
        overlay_mode = "grammar"
    # Default annotated/plain from the reader's saved preference; the explicit
    # ?annotate= query param always wins when present.
    default_annotate = "1" if _core.current_preferences().annotated_by_default else "0"
    annotate = request.args.get("annotate", default_annotate) != "0"
    n_sent = _core.q1("SELECT count(*) n FROM sentences WHERE document_id = %s", (str(doc_id),))[
        "n"
    ]
    sentence_base_sql = (
        "SELECT sen.ordinal, sen.text, sp.char_start AS ss, sp.char_end AS se, "
        "sp.page_number "
        "FROM sentences sen JOIN spans sp ON sp.id = sen.span_id "
        "WHERE sen.document_id = %s"
    )
    sentence_sql = f"{sentence_base_sql} ORDER BY sen.ordinal"
    page_rows = []
    page_numbers: list[int] = []
    if not full_view:
        page_rows = _core.q(
            """
            SELECT page_number
            FROM (
                SELECT DISTINCT sp.page_number
                FROM sentences sen JOIN spans sp ON sp.id = sen.span_id
                WHERE sen.document_id = %s AND sp.page_number IS NOT NULL
                UNION
                SELECT dp.page_number
                FROM document_pages dp
                WHERE dp.document_id = %s
            ) pages_with_geometry
            ORDER BY page_number
            """,
            (str(doc_id), str(doc_id)),
        )
        page_numbers = [int(row["page_number"]) for row in page_rows]

    if full_view:
        sentences = _core.q(sentence_sql, (str(doc_id),))
        page = 1
        pages = 1
    else:
        if page_numbers:
            pages = max(page_numbers + [int(doc.get("page_count") or 0)])
            page = min(page, pages)
            sentences = _core.q(
                f"{sentence_base_sql} AND sp.page_number = %s ORDER BY sen.ordinal",
                (str(doc_id), page),
            )
        else:
            sentences = _core.q(
                f"{sentence_sql} LIMIT %s OFFSET %s",
                (str(doc_id), per_page, (page - 1) * per_page),
            )
            pages = max(1, -(-n_sent // per_page))

    attach_page_blocks(_core.q, _core.q1, sentences, doc_id, annotate=annotate)
    overlay = None
    if original_view:
        overlay = (
            original_overlay(doc, doc_id, page, page_numbers, overlay_mode)
            if doc.get("doc_type") == "pdf"
            else original_markup(doc, doc_id, overlay_mode)
        )

    metadata_history = _core.q(
        "SELECT field, old_value, new_value, changed_at, actor "
        "FROM document_metadata_history WHERE document_id = %s ORDER BY id DESC",
        (str(doc_id),),
    )
    metadata_suggestions = _core.q(
        "SELECT id, field, current_value, suggested_value, confidence, reason, source, status "
        "FROM document_metadata_suggestion WHERE document_id = %s "
        "ORDER BY status, confidence DESC, field, suggested_value",
        (str(doc_id),),
    )
    for suggestion in metadata_suggestions:
        confidence = float(suggestion.get("confidence") or 0)
        if confidence >= 0.85:
            suggestion["confidence_label"] = "High confidence"
        elif confidence >= 0.75:
            suggestion["confidence_label"] = "Medium confidence"
        else:
            suggestion["confidence_label"] = "Low confidence"
    return _core.render_template(
        "document.html",
        doc=doc,
        authors=[a["name"] for a in authors],
        tags=[t["tag"] for t in tags],
        pos=pos,
        top_verbs=top_verbs,
        preview=document_preview(doc),
        manual_collections=available_manual_collections(doc_id),
        sentences=sentences,
        page=page,
        pages=pages,
        full_view=full_view,
        original_view=original_view,
        overlay=overlay,
        overlay_mode=overlay_mode,
        n_sent=n_sent,
        annotate=annotate,
        annotation_state=latest_annotation_state(_core.q, doc_id),
        metadata_history=metadata_history,
        metadata_suggestions=metadata_suggestions,
        metadata_error=request.args.get("metadata_error"),
        metadata_queued=request.args.get("metadata_queued"),
        suggestions_error=request.args.get("suggestions_error"),
        suggestions_queued=request.args.get("suggestions_queued"),
        reprocess_steps=reprocess_steps(doc, str(doc_id)),
        reprocess_queued=request.args.get("reprocess_queued"),
        reprocess_error=request.args.get("reprocess_error"),
    )


def document_reprocess(doc_id):
    """Queue one document pipeline step again through the GraphQL API."""
    doc = _core.q1("SELECT * FROM documents WHERE id = %s", (str(doc_id),))
    if doc is None:
        abort(404, "Document not found.")
    step = request.form.get("step", "")
    cfg = _REPROCESS_CONFIG.get(step)
    if cfg is None:
        return redirect(url_for("document", doc_id=doc_id, reprocess_error="unknown step"))
    if cfg["pdf_only"] and str(doc.get("doc_type") or "").lower() != "pdf":
        return redirect(
            url_for("document", doc_id=doc_id, reprocess_error="re-extract is PDF-only")
        )
    active = active_reprocess_jobs(str(doc_id)).get(step)
    if active is not None:
        return redirect(
            url_for(
                "document",
                doc_id=doc_id,
                reprocess_error=f"{cfg['label']} is already {active['status']}",
            )
        )
    try:
        _core.gql(cfg["mutation"], {"id": str(doc_id)})
    except Exception as exc:
        current_app.logger.exception("document reprocess action failed")
        return redirect(url_for("document", doc_id=doc_id, reprocess_error=str(exc)))
    return redirect(url_for("document", doc_id=doc_id, reprocess_queued=step))


def document_metadata_suggestions_request(doc_id):
    """Queue verified document-metadata suggestions for human review."""
    try:
        _core.gql(
            """
            mutation SuggestDocumentMetadata($id: ID!) {
              suggestDocumentMetadata(documentId: $id) { id }
            }
            """,
            {"id": str(doc_id)},
        )
    except Exception as exc:
        return redirect(url_for("document", doc_id=doc_id, suggestions_error=str(exc)))
    return redirect(url_for("document", doc_id=doc_id, suggestions_queued=1))


def document_metadata_update(doc_id):
    """Submit a title/description/tags edit via the GraphQL mutation (BL-058).

    The edit is enqueued, not applied synchronously (D-004/D-005: writes never
    happen in the request) — the worker typically catches up within
    WORKER_POLL_SECONDS (5s by default); flag that on the redirect rather than
    let the reloaded page look like the edit silently did nothing.
    """
    error = submit_metadata_update(_core.gql, doc_id, request.form)
    if error is not None:
        return redirect(url_for("document", doc_id=doc_id, metadata_error=error))
    return redirect(url_for("document", doc_id=doc_id, metadata_queued=1))


def _stream_api_response(url: str, expected_statuses: tuple[int, ...]) -> tuple:
    """Open a streaming GET against the API; abort on the expected error statuses."""
    import httpx

    client = httpx.Client(timeout=None)
    upstream = client.send(
        client.build_request("GET", url, headers=api_auth_headers()), stream=True
    )
    if upstream.status_code in expected_statuses:
        status = upstream.status_code
        upstream.close()
        client.close()
        abort(status)
    upstream.raise_for_status()

    def _pump():
        try:
            yield from upstream.iter_bytes(chunk_size=1 << 20)
        finally:
            upstream.close()
            client.close()

    return upstream, _pump


def document_download(doc_id):
    """Stream an original document download through the API boundary."""
    base = API_URL.rsplit("/graphql", 1)[0]
    upstream, pump = _stream_api_response(
        f"{base}/documents/{doc_id}/download", (401, 403, 404, 409)
    )
    headers = {}
    if upstream.headers.get("content-disposition"):
        headers["Content-Disposition"] = upstream.headers["content-disposition"]
    return Response(
        stream_with_context(pump()),
        mimetype=upstream.headers.get("content-type", "application/octet-stream"),
        headers=headers,
    )


def document_page_image(doc_id, page_number):
    """Stream a rendered original-PDF page PNG through the API boundary."""
    base = API_URL.rsplit("/graphql", 1)[0]
    upstream, pump = _stream_api_response(
        f"{base}/documents/{doc_id}/pages/{page_number}.png", (401, 403, 404, 409, 415)
    )
    headers = {}
    if upstream.headers.get("x-docray-cache"):
        headers["X-DocRay-Cache"] = upstream.headers["x-docray-cache"]
    return Response(
        stream_with_context(pump()),
        mimetype=upstream.headers.get("content-type", "image/png"),
        headers=headers,
    )


def document_cover(doc_id):
    """Stream a small WebP first-page cover through the API boundary.

    Lets list surfaces render a ~15 KB thumbnail instead of proxying the full
    144-DPI page PNG. ``w`` is forwarded and the API clamps it to its allowlist.
    """
    base = API_URL.rsplit("/graphql", 1)[0]
    width = request.args.get("w", type=int)
    query = f"?w={width}" if width else ""
    upstream, pump = _stream_api_response(
        f"{base}/documents/{doc_id}/cover.webp{query}", (401, 403, 404, 409, 415)
    )
    headers = {}
    for header in ("cache-control", "x-docray-cache"):
        if upstream.headers.get(header):
            headers[header.title()] = upstream.headers[header]
    return Response(
        stream_with_context(pump()),
        mimetype=upstream.headers.get("content-type", "image/webp"),
        headers=headers,
    )


def register(app, core: ModuleType) -> None:
    """Attach the single-document routes to *app*, late-binding *core* (D-015 split)."""
    global _core
    _core = core
    app.add_url_rule("/doc/<uuid:doc_id>", "document", document)
    app.add_url_rule(
        "/doc/<uuid:doc_id>/reprocess", "document_reprocess", document_reprocess, methods=["POST"]
    )
    app.add_url_rule(
        "/doc/<uuid:doc_id>/metadata-suggestions",
        "document_metadata_suggestions_request",
        document_metadata_suggestions_request,
        methods=["POST"],
    )
    app.add_url_rule(
        "/doc/<uuid:doc_id>/metadata",
        "document_metadata_update",
        document_metadata_update,
        methods=["POST"],
    )
    app.add_url_rule("/doc/<uuid:doc_id>/download", "document_download", document_download)
    app.add_url_rule(
        "/doc/<uuid:doc_id>/page/<int:page_number>.png",
        "document_page_image",
        document_page_image,
    )
    app.add_url_rule("/doc/<uuid:doc_id>/cover.webp", "document_cover", document_cover)
